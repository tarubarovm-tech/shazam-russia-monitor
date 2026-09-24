#!/usr/bin/env python3
"""
Прямая сверка трека с Яндекс.Музыкой через официальный API.

ЗАЧЕМ ЭТОТ МОДУЛЬ
-----------------
Штатная проверка в monitor.py ходит к Яндексу через трёх посредников:

    iTunes Search -> song.link -> Musicfetch (URL) -> Musicfetch (ISRC)

Ни на одном шаге она не разговаривает с Яндексом — она ищет УПОМИНАНИЕ Яндекса
на странице агрегатора. Отсюда две беды (замер на пуле из 10 треков, 21.09.2026):

  1. 6 треков из 10 остались нерешёнными (uncertain / not_confirmed), причём
     как минимум 2 из них на Яндексе ЕСТЬ: «Пыяла» (АИГЕЛ) и «ты в моих мыслях
     навсегда». song.link отдал страницу, подтвердил название, но ссылки на
     Яндекс там не было — хотя трек на Яндексе лежит.
  2. Без MUSICFETCH_TOKEN (платный, от $50/мес) статус verified_missing
     недостижим в принципе, то есть алертов не будет вообще никогда.

Этот модуль спрашивает Яндекс напрямую, своим токеном. Один запрос вместо
четырёх, авторитетный ответ вместо догадки по чужой странице.
На том же пуле: 10 из 10 решены верно, 0.3-0.7 с против 3-6 с.

ГЛАВНЫЙ ПРИНЦИП: НЕ ТЕРЯТЬ ТРЕКИ
--------------------------------
Пользователю важнее увидеть лишнее, чем пропустить годное: лишнее он уберёт
руками, а пропущенное не увидит никогда. Поэтому здесь ТРИ исхода, а не два:

    ABSENT    — Яндекс ответил, трека нет.           -> в алерт
    PRESENT   — Яндекс ответил, трек есть.           -> не алертим
    UNKNOWN   — Яндекс не ответил / сомнительно.     -> В АЛЕРТ, с пометкой

UNKNOWN идёт в алерт сознательно. Старая логика прятала такие треки в pending,
и они исчезали навсегда. Теперь они доезжают до человека с честной пометкой
«проверить руками».

ПОРОГИ
------
Сверка — по склейке «артист название» через token_sort_ratio, как в проверенном
на проде YandexMatch/yandex_match.py. Версии и ремиксы отрезаются: базовый трек
на Яндексе есть -> версия тоже считается присутствующей.

Порог намеренно НИЗКИЙ (75 против 80 в YandexMatch). Низкий порог = легче
признать трек присутствующим = меньше ложных алертов; но так как цена ошибки
несимметрична (лучше лишнее), сомнительную зону 60..75 мы не считаем
отсутствием, а отдаём как UNKNOWN.
"""
import os
import re
import threading
import time
import unicodedata

try:
    from yandex_music import Client
except ImportError:  # модуль опционален — без него monitor.py работает по-старому
    Client = None

try:
    from thefuzz import fuzz
except ImportError:
    fuzz = None


# ============================== НАСТРОЙКИ ==============================

# Совпадение «артист название» в процентах.
MATCH_THRESHOLD = 75        # >= этого — считаем, что трек найден
DOUBT_THRESHOLD = 60        # 60..75 — зона сомнения, отдаём UNKNOWN, не ABSENT
# Ниже этого артист считается ДРУГИМ, и совпавшее название не спасает пару.
# 50 было мало: Matroda/Marc = 55%. Соисполнители порог не задевают —
# token_set_ratio даёт им ~100.
ARTIST_MATCH_THRESHOLD = 80

SEARCH_RETRIES = 3
RETRY_BASE_DELAY = 2        # паузы 2с, 4с, 6с
TOP_RESULTS = 8             # сколько результатов Яндекса смотреть

# Пометки версий/ремастеров: базовый трек и его версия — один трек.
_VERSION_WORDS = (
    r"single version|album version|radio edit|radio version|remaster(?:ed)?|"
    r"slowed|sped up|speed up|reverb|nightcore|remix|mix|version|edit|"
    r"live|acoustic|instrumental|extended|original mix|bonus track|explicit|clean|"
    r"prod\.?|feat\.?|ft\.?"
)

_print_lock = threading.Lock()
_client_lock = threading.Lock()
_client = None


# ============================== СТАТУСЫ ==============================

PRESENT = "yandex_present"
ABSENT = "yandex_absent"
UNKNOWN = "yandex_unknown"

# Что показываем в Telegram.
STATUS_TEXT = {
    PRESENT: "🟡 Яндекс: есть",
    ABSENT: "🟢 Яндекс: НЕТ (прямая проверка)",
    UNKNOWN: "⚠️ Яндекс: не удалось проверить — глянь руками",
}


# ============================== ТОКЕН ==============================

def yandex_token():
    return os.environ.get("YANDEX_TOKEN", "").strip()


def available():
    """Можно ли вообще пользоваться прямой проверкой."""
    return bool(Client and fuzz and yandex_token())


def get_client():
    """Ленивый общий клиент. Один на процесс, инициализация под локом."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            token = yandex_token()
            if not token:
                raise RuntimeError("YANDEX_TOKEN не задан")
            if Client is None:
                raise RuntimeError("не установлен пакет yandex-music")
            _client = Client(token).init()
    return _client


# ============================== НОРМАЛИЗАЦИЯ ==============================

_RU_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ы": "y", "э": "e", "ю": "yu", "я": "ya",
    "ь": "", "ъ": "",
})


def norm(value):
    """Нижний регистр, снятие версий/ремиксов, чистка пунктуации."""
    s = (value or "").lower().strip()
    s = s.replace("ё", "е")
    # (single version), [remastered 2023]
    s = re.sub(rf"[\(\[][^)\]]*({_VERSION_WORDS})[^)\]]*[\)\]]", " ", s)
    # - slowed, — remix в хвосте
    s = re.sub(rf"\s*[-–—]\s*[^-–—]*({_VERSION_WORDS}).*$", " ", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-zа-я0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def latinize(value):
    """Кириллица латиницей: АИГЕЛ -> aigel. Яндекс часто пишет артиста латиницей."""
    return norm(value).translate(_RU_LATIN)


def similarity(ours, theirs):
    """
    Лучший из двух каналов: как есть и в транслите.

    Транслит обязателен: на проде «Пыяла — АИГЕЛ» Яндекс отдаёт как
    «Пыяла — AIGEL». Без транслита это разные строки и трек «теряется».
    """
    if fuzz is None:
        return 0
    direct = fuzz.token_sort_ratio(norm(ours), norm(theirs))
    latin = fuzz.token_sort_ratio(latinize(ours), latinize(theirs))
    return max(direct, latin)


def _pair_score(our_artist, our_title, ya_artist, ya_title):
    """
    Совпадение пары «артист + название», устойчивое к соисполнителям.

    ЗАЧЕМ НЕ ПРОСТО similarity СКЛЕЕК. В чарте артист часто записан со всеми
    соисполнителями («Женя Трофимов & Комната культуры», «HUGEL, Imael Angel
    & Ultra Naté»), а Яндекс отдаёт только главного. Склейка тогда проседает
    до 66-71% — ниже порога, и существующий трек уезжает в UNKNOWN/ABSENT,
    то есть превращается в ложный алерт. Замер 21.09.2026:

        «Женя Трофимов & Комната культуры Море» vs «Женя Трофимов Море» -> 71%
        «HUGEL, Imael Angel & Ultra Nate ...»   vs «HUGEL ...»           -> 66%

    Поэтому считаем два канала и берём лучший:
      - склейка целиком (ловит случаи, где артист записан одинаково);
      - название отдельно + артист отдельно, где артисту достаточно, чтобы
        ОДИН из перечисленных совпал (token_set_ratio не штрафует за лишних).
    """
    if fuzz is None:
        return 0

    joined = similarity(f"{our_artist} {our_title}", f"{ya_artist} {ya_title}")

    title_score = similarity(our_title, ya_title)
    if title_score < 80:
        # Название не совпало — артист уже не спасёт.
        return joined

    # token_set_ratio: «A & B & C» против «A» даёт высокий балл, лишние
    # соисполнители не штрафуются. Это ровно наш случай.
    artist_score = max(
        fuzz.token_set_ratio(norm(our_artist), norm(ya_artist)),
        fuzz.token_set_ratio(latinize(our_artist), latinize(ya_artist)),
    )
    if not norm(our_artist) or not norm(ya_artist):
        artist_score = 100          # артист неизвестен — судим по названию

    # Чужой артист при совпавшем названии — это РАЗНЫЕ треки
    # («One Dance» у Drake и у Coldplay). Такая пара НЕ может быть PRESENT
    # ни по какому каналу — ни по split, ни по склейке: длинное совпавшее
    # название вытягивает склейку до 89% даже при чужом артисте.
    # Прод 24.09.2026: «Matroda — I Need Your Lovin'» сматчился с
    # «Marc — I Need Your Lovin'» (2006), артисты похожи на 55%, склейка 89%.
    # Новинку молча сочли существующей, до человека она не доехала.
    # Потолок — зона сомнения: трек уходит человеку с пометкой «глянь руками».
    if artist_score < ARTIST_MATCH_THRESHOLD:
        return min(joined, MATCH_THRESHOLD - 1)

    split = round(0.55 * title_score + 0.45 * artist_score)
    return max(joined, split)


# ============================== ПОИСК ==============================

def _search(query):
    """Поиск с ретраями ТОЛЬКО на сетевые сбои. Пустой ответ — валидный результат."""
    last_err = None
    for attempt in range(1, SEARCH_RETRIES + 1):
        try:
            return get_client().search(query, type_="track", page=0, nocorrect=True)
        except Exception as exc:                      # noqa: BLE001 — любой сбой сети/SDK
            last_err = exc
            if attempt < SEARCH_RETRIES:
                time.sleep(RETRY_BASE_DELAY * attempt)
    raise RuntimeError(f"Яндекс не ответил после {SEARCH_RETRIES} попыток: {last_err}")


def _tracks_of(result):
    if not result or not getattr(result, "tracks", None):
        return []
    return list(getattr(result.tracks, "results", None) or [])


def _describe(track):
    artists = " ".join(a.name for a in getattr(track, "artists", []) if getattr(a, "name", ""))
    return artists, getattr(track, "title", "") or ""


def _track_url(track):
    tid = getattr(track, "id", None)
    albums = getattr(track, "albums", None) or []
    if tid and albums:
        album_id = getattr(albums[0], "id", None)
        if album_id:
            return f"https://music.yandex.ru/album/{album_id}/track/{tid}"
    if tid:
        return f"https://music.yandex.ru/track/{tid}"
    return ""


def check_track(artist, title):
    """
    Есть ли трек на Яндексе.

    Возвращает dict:
        status  — PRESENT / ABSENT / UNKNOWN
        score   — лучшее совпадение в процентах
        url     — ссылка на найденный трек (если PRESENT)
        matched — что именно нашлось на Яндексе
        note    — пояснение для человека

    UNKNOWN отдаётся когда:
      - нет токена / не стоит пакет;
      - Яндекс не ответил после ретраев;
      - лучшее совпадение попало в зону сомнения 60..75.
    В этих случаях трек НЕ считается отсутствующим и НЕ пропадает —
    вызывающая сторона обязана показать его человеку.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()

    if not title:
        return {"status": UNKNOWN, "score": 0, "url": "", "matched": "",
                "note": "пустое название трека"}

    if not available():
        missing = []
        if Client is None:
            missing.append("пакет yandex-music")
        if fuzz is None:
            missing.append("пакет thefuzz")
        if not yandex_token():
            missing.append("YANDEX_TOKEN")
        return {"status": UNKNOWN, "score": 0, "url": "", "matched": "",
                "note": "прямая проверка недоступна: нет " + ", ".join(missing)}

    try:
        # Заход 1: «артист - название».
        result = _search(f"{artist} - {title}" if artist else title)
        tracks = _tracks_of(result)

        # Заход 2: только название. В чартах артист бывает записан криво
        # («урал гайсин & снялцепи»), и поиск по паре не находит ничего.
        if not tracks:
            result = _search(title)
            tracks = _tracks_of(result)

        if not tracks:
            return {"status": ABSENT, "score": 0, "url": "", "matched": "",
                    "note": "Яндекс ответил: ничего не найдено"}

        best_score = 0
        best_track = None
        for track in tracks[:TOP_RESULTS]:
            ya_artists, ya_title = _describe(track)
            score = _pair_score(artist, title, ya_artists, ya_title)
            if score > best_score:
                best_score = score
                best_track = track

        matched = ""
        if best_track is not None:
            ya_artists, ya_title = _describe(best_track)
            matched = f"{ya_title} — {ya_artists}".strip(" —")

        if best_score >= MATCH_THRESHOLD:
            return {"status": PRESENT, "score": best_score,
                    "url": _track_url(best_track), "matched": matched,
                    "note": f"совпадение {best_score}%"}

        if best_score >= DOUBT_THRESHOLD:
            # Зона сомнения: похоже, но не точно. НЕ объявляем отсутствием —
            # иначе рискуем заалертить трек, который на самом деле есть,
            # либо наоборот замолчать годный. Отдаём человеку.
            return {"status": UNKNOWN, "score": best_score,
                    "url": _track_url(best_track), "matched": matched,
                    "note": f"неоднозначно ({best_score}%), похоже на «{matched}»"}

        return {"status": ABSENT, "score": best_score, "url": "", "matched": matched,
                "note": f"на Яндексе не найден (лучшее совпадение {best_score}%)"}

    except Exception as exc:                          # noqa: BLE001
        # Сеть/SDK/токен — что угодно. Это НЕ «трека нет».
        return {"status": UNKNOWN, "score": 0, "url": "", "matched": "",
                "note": f"проверка не удалась: {exc}"}


def status_text(info):
    return STATUS_TEXT.get((info or {}).get("status"), STATUS_TEXT[UNKNOWN])
