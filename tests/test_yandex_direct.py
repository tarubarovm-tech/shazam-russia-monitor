"""Тесты прямой сверки с Яндексом.

Главное, что здесь проверяется, — НЕ ТЕРЯТЬ ТРЕКИ. Любая неопределённость
обязана давать UNKNOWN и доезжать до человека, а не превращаться в молчание.
"""
import unittest
from unittest.mock import patch

import yandex_direct as yd


class _Artist:
    def __init__(self, name):
        self.name = name


class _Album:
    def __init__(self, album_id):
        self.id = album_id


class _Track:
    def __init__(self, title, artists, track_id=1, album_id=10):
        self.title = title
        self.artists = [_Artist(a) for a in artists]
        self.id = track_id
        self.albums = [_Album(album_id)]


class _Tracks:
    def __init__(self, results):
        self.results = results


class _Result:
    def __init__(self, tracks):
        self.tracks = _Tracks(tracks) if tracks is not None else None


def _search_returning(*batches):
    """Заглушка поиска: отдаёт заготовленные ответы по очереди."""
    calls = list(batches)

    def _fake(query):
        return calls.pop(0) if calls else _Result([])

    return _fake


class NormalizationTests(unittest.TestCase):
    def test_version_suffixes_are_stripped(self):
        self.assertEqual(yd.norm("Lights (Single Version)"), "lights")
        self.assertEqual(yd.norm("Blinding Lights (Remix)"), "blinding lights")
        self.assertEqual(yd.norm("Shape of You - Slowed"), "shape of you")
        self.assertEqual(yd.norm("Song [Remastered 2023]"), "song")

    def test_yo_is_folded(self):
        self.assertEqual(yd.norm("Ёлка"), yd.norm("Елка"))

    def test_latinize_maps_cyrillic(self):
        self.assertEqual(yd.latinize("АИГЕЛ"), "aigel")
        self.assertEqual(yd.latinize("Пыяла"), "pyyala")

    def test_similarity_bridges_cyrillic_and_latin(self):
        """АИГЕЛ на Яндексе записан как AIGEL — без транслита трек терялся."""
        score = yd.similarity("АИГЕЛ Пыяла", "AIGEL Пыяла")
        self.assertGreaterEqual(score, yd.MATCH_THRESHOLD)

    def test_similarity_rejects_different_track(self):
        score = yd.similarity("Imagine Dragons Believer", "Imagine Dragons Thunder")
        self.assertLess(score, yd.MATCH_THRESHOLD)


class CheckTrackTests(unittest.TestCase):
    def setUp(self):
        self._avail = patch.object(yd, "available", return_value=True)
        self._avail.start()
        self.addCleanup(self._avail.stop)

    def test_exact_match_is_present(self):
        fake = _search_returning(_Result([_Track("Пыяла", ["AIGEL"])]))
        with patch.object(yd, "_search", side_effect=fake):
            info = yd.check_track("АИГЕЛ", "Пыяла")
        self.assertEqual(info["status"], yd.PRESENT)
        self.assertIn("music.yandex.ru", info["url"])

    def test_version_of_existing_track_is_present(self):
        """Базовый трек на Яндексе есть -> ремикс тоже считается присутствующим."""
        fake = _search_returning(_Result([_Track("Blinding Lights", ["The Weeknd"])]))
        with patch.object(yd, "_search", side_effect=fake):
            info = yd.check_track("The Weeknd", "Blinding Lights (Remix)")
        self.assertEqual(info["status"], yd.PRESENT)

    def test_empty_answer_is_absent(self):
        fake = _search_returning(_Result([]), _Result([]))
        with patch.object(yd, "_search", side_effect=fake):
            info = yd.check_track("Fakeband Zzz", "Qwzzz Vibrations")
        self.assertEqual(info["status"], yd.ABSENT)

    def test_title_only_retry_when_artist_is_mangled(self):
        """В чартах артист бывает записан криво — второй заход по одному названию."""
        fake = _search_returning(
            _Result([]),                                    # «артист - название» пусто
            _Result([_Track("Море", ["Женя Трофимов"])]),   # только название — нашлось
        )
        with patch.object(yd, "_search", side_effect=fake):
            info = yd.check_track("Женя Трофимов & Комната культуры", "Море")
        self.assertEqual(info["status"], yd.PRESENT)

    def test_featured_artists_do_not_sink_the_match(self):
        """
        В чарте артист со всеми соисполнителями, на Яндексе — только главный.
        Склейка целиком давала 66-71% (ниже порога), и существующий трек
        уезжал в UNKNOWN. Проверяем, что теперь он PRESENT.
        """
        cases = [
            ("Женя Трофимов & Комната культуры", "Море", "Женя Трофимов", "Море"),
            ("HUGEL, Imael Angel & Ultra Naté", "Movin' To The Sun", "HUGEL", "Movin' To The Sun"),
            ("урал гайсин & снялцепи", "ты в моих мыслях навсегда", "урал гайсин", "ты в моих мыслях навсегда"),
        ]
        for our_artist, our_title, ya_artist, ya_title in cases:
            with self.subTest(artist=our_artist):
                score = yd._pair_score(our_artist, our_title, ya_artist, ya_title)
                self.assertGreaterEqual(
                    score, yd.MATCH_THRESHOLD,
                    f"{our_artist} / {ya_artist}: {score}% — существующий трек потерян",
                )

    def test_same_title_different_artist_is_not_present(self):
        """
        «One Dance» у Drake и у Coldplay — разные треки. Нельзя считать такую
        пару совпадением: новинка молча стала бы «уже есть на Яндексе»
        и до человека не доехала.
        """
        cases = [
            ("Drake", "One Dance", "Coldplay", "One Dance"),
            ("Imagine Dragons", "Believer", "Imagine Dragons", "Thunder"),
            ("Тимати", "Ах какая женщина", "Кавер Группа Нонейм", "Ах какая женщина"),
        ]
        for our_artist, our_title, ya_artist, ya_title in cases:
            with self.subTest(pair=f"{our_artist}/{ya_artist}"):
                score = yd._pair_score(our_artist, our_title, ya_artist, ya_title)
                self.assertLess(
                    score, yd.MATCH_THRESHOLD,
                    f"{our_artist} ошибочно сматчен с {ya_artist} ({score}%)",
                )

    # --- Ключевая группа: ничего не теряем ---

    def test_network_failure_is_unknown_not_absent(self):
        """Яндекс не ответил — это НЕ «трека нет». Иначе посыплются ложные алерты."""
        with patch.object(yd, "_search", side_effect=RuntimeError("таймаут")):
            info = yd.check_track("Artist", "Song")
        self.assertEqual(info["status"], yd.UNKNOWN)
        self.assertNotEqual(info["status"], yd.ABSENT)

    def test_doubt_zone_is_unknown_not_absent(self):
        """Похоже, но не точно — отдаём человеку, а не объявляем отсутствием."""
        fake = _search_returning(_Result([_Track("Песня про лето", ["Другой Артист"])]))
        with patch.object(yd, "_search", side_effect=fake):
            info = yd.check_track("Некий Артист", "Песня про лето")
        self.assertIn(info["status"], (yd.UNKNOWN, yd.PRESENT))
        self.assertNotEqual(info["status"], yd.ABSENT)

    def test_missing_token_is_unknown_not_absent(self):
        """Без токена нельзя молча считать, что треков нет на Яндексе."""
        self._avail.stop()
        with patch.object(yd, "available", return_value=False):
            info = yd.check_track("Artist", "Song")
        self._avail.start()
        self.assertEqual(info["status"], yd.UNKNOWN)
        self.assertIn("недоступна", info["note"])

    def test_empty_title_is_unknown(self):
        info = yd.check_track("Artist", "")
        self.assertEqual(info["status"], yd.UNKNOWN)

    def test_status_text_always_resolves(self):
        for status in (yd.PRESENT, yd.ABSENT, yd.UNKNOWN):
            self.assertTrue(yd.status_text({"status": status}))
        self.assertTrue(yd.status_text(None))
        self.assertTrue(yd.status_text({"status": "мусор"}))


if __name__ == "__main__":
    unittest.main()
