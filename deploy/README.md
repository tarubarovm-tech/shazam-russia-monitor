# Развёртывание на своём сервере

Монитор работает на сервере (Hetzner CPX12, Helsinki) под systemd-таймером.
Расписание в GitHub Actions отключено — иначе мониторов было бы два и они
слали бы дубли.

## Что здесь лежит

| Файл | Куда ставится |
|---|---|
| `run.sh` | `/opt/shazam-monitor/run.sh` |
| `shazam-monitor.service` | `/etc/systemd/system/` |
| `shazam-monitor.timer` | `/etc/systemd/system/` |

**Эти файлы сервер сам НЕ подтягивает.** `run.sh` делает `git fetch` для кода
монитора, но systemd-юниты лежат вне репозитория: после правки таймера здесь
его нужно скопировать на сервер руками (см. ниже).

## Расписание

| Период | Интервал | Прогонов в сутки |
|---|---|---|
| 08:00–12:00 МСК (05:00–09:00 UTC) | 3 минуты | 80 |
| остальные 20 часов | 20 минут | 60 |
| **итого** | | **140** |

Окно выбрано по наблюдениям 22.09.2026: реальные обновления составов чартов
пришли в 09:09, 09:43 и 10:55 МСК — все внутри окна. Вне окна обновлений
пока не фиксировалось, но 20-минутный интервал оставлен как страховка:
данных всего за сутки.

## Первичная установка

```bash
# 1. код и окружение
mkdir -p /opt && cd /opt
git clone https://github.com/tarubarovm-tech/shazam-russia-monitor.git shazam-monitor
cd shazam-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. секреты (права 600!)
cat > .env <<'EOF'
BOT_TOKEN=...
CHAT_ID=...
YANDEX_TOKEN=...
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
EOF
chmod 600 .env

# 3. состояние не должно уезжать в git
printf 'chart_state.json\n.env\n.venv/\n' >> .gitignore
git update-index --skip-worktree chart_state.json

# 4. запуск по расписанию
cp deploy/run.sh /opt/shazam-monitor/run.sh && chmod +x /opt/shazam-monitor/run.sh
cp deploy/shazam-monitor.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now shazam-monitor.timer
```

## Обязательный шаг: приоритет IPv4

```bash
echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf
```

Без этого Telegram висит 30 секунд на каждом запросе: первая попытка идёт
по IPv6, где api.telegram.org с этого хоста недоступен, и только после
таймаута система откатывается на IPv4. Проверено 22.09.2026 — было 30.3с,
стало 0.4с.

## Обновление расписания

```bash
scp deploy/shazam-monitor.timer root@СЕРВЕР:/etc/systemd/system/
ssh root@СЕРВЕР 'systemctl daemon-reload && systemctl restart shazam-monitor.timer'
```

## Управление

```bash
systemctl list-timers shazam-monitor.timer   # когда следующий запуск
tail -f /var/log/shazam-monitor.log          # лог вживую
/opt/shazam-monitor/run.sh                   # прогнать сейчас
systemctl stop shazam-monitor.timer          # приостановить
```

## Как обновляется код

`run.sh` перед каждым прогоном делает `git fetch` и, если в `origin/main`
появилось новое, подтягивает и переустанавливает зависимости. То есть push
в репозиторий доезжает до сервера сам — в пределах интервала расписания.

`chart_state.json` и `.env` при этом не трогаются: они в `.gitignore`.
