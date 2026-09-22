#!/bin/bash
# Один прогон монитора. Вызывается systemd-таймером каждые 10 минут.
#
# Состояние (chart_state.json) остаётся ЛОКАЛЬНЫМ файлом и в git не
# коммитится: на GitHub Actions это было нужно, чтобы оно пережило
# эфемерный раннер, здесь просто лежит на диске.
set -u
cd /opt/shazam-monitor || exit 1

LOG=/var/log/shazam-monitor.log
echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ===" >> "$LOG"

# Подтянуть свежий код. Состояние при этом не трогаем: оно не отслеживается
# git (см. .gitignore), поэтому reset --hard его не затрёт.
if git fetch -q origin main 2>>"$LOG"; then
  local_sha=$(git rev-parse HEAD)
  remote_sha=$(git rev-parse origin/main)
  if [ "$local_sha" != "$remote_sha" ]; then
    echo "[git] обновляю код: ${local_sha:0:7} -> ${remote_sha:0:7}" >> "$LOG"
    git reset -q --hard origin/main 2>>"$LOG"
    # requirements могли поменяться
    .venv/bin/pip install -q -r requirements.txt >>"$LOG" 2>&1
  fi
fi

# секреты
set -a
. ./.env
set +a

timeout 600 .venv/bin/python monitor.py >> "$LOG" 2>&1
code=$?
[ $code -ne 0 ] && echo "[!] monitor.py вышел с кодом $code" >> "$LOG"

# лог не должен расти бесконечно
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 10485760 ]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
exit $code
