#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${PROJECT_DIR}/install.sh"

fail(){ printf 'Ошибка small-VPS установки: %s\n' "$1" >&2; exit 1; }
[[ "${EUID}" -eq 0 ]] || fail "запустите через sudo"
[[ -f "${INSTALLER}" ]] || fail "install.sh не найден"

mem_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
swap_kb="$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
disk_kb="$(df -Pk "${PROJECT_DIR}" | awk 'NR==2{print $4}')"

printf 'SMALL-VPS режим: RAM %s МБ, swap %s МБ, свободный диск %.1f ГБ.\n' \
  "$((mem_kb/1024))" "$((swap_kb/1024))" "$(awk -v k="$disk_kb" 'BEGIN{printf "%.1f", k/1024/1024}')"

(( mem_kb >= 1700000 )) || fail "для этого профиля нужно около 2 ГБ RAM"
(( swap_kb >= 1500000 )) || fail "нужен swap минимум около 1.5 ГБ; запустите one_click_install.sh, он настроит swap"

# Для 2 ГБ RAM всегда экономный runtime.
export UVICORN_WORKERS=1
export CELERY_CONCURRENCY=1
export COMPOSE_PARALLEL_LIMIT=1

# Безопасно освобождаем только кэши. Volumes/БД не трогаем.
apt-get clean >/dev/null 2>&1 || true
if command -v docker >/dev/null 2>&1; then
  if ! docker ps -q 2>/dev/null | grep -q .; then
    docker builder prune -af >/dev/null 2>&1 || true
    docker image prune -f >/dev/null 2>&1 || true
  fi
fi

disk_kb="$(df -Pk "${PROJECT_DIR}" | awk 'NR==2{print $4}')"
(( disk_kb >= 3200000 )) || fail "после очистки нужно хотя бы около 3.2 ГБ свободного диска; сейчас $(awk -v k="$disk_kb" 'BEGIN{printf "%.1f", k/1024/1024}') ГБ"

# В текущем универсальном install.sh исторически остался порог 10 ГБ.
# На small-VPS заменяем только этот конкретный preflight-блок локально;
# пользовательские данные, env и volumes не меняются.
python3 - "${INSTALLER}" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = '''  if (( disk_kb < 10000000 )); then\n    fail "нужно минимум около 10 ГБ свободного диска"\n  fi'''
new = '''  if (( disk_kb < 3200000 )); then\n    fail "для SMALL-VPS режима нужно минимум около 3.2 ГБ свободного диска"\n  elif (( disk_kb < 6000000 )); then\n    printf 'SMALL-VPS/LOW-DISK режим: свободного диска меньше 6 ГБ; сборка будет последовательной, временные кэши будут очищаться.\\n' >&2\n    LOW_MEMORY_MODE=true\n    UVICORN_WORKERS=1\n    CELERY_CONCURRENCY=1\n    export COMPOSE_PARALLEL_LIMIT=1\n  fi'''
if old in s:
    s = s.replace(old, new, 1)
elif 'нужно минимум около 10 ГБ свободного диска' in s:
    raise SystemExit('Не удалось безопасно заменить старый disk-check: формат install.sh изменился')
p.write_text(s)
PY

bash -n "${INSTALLER}" || fail "install.sh повреждён после адаптации"
printf 'Запускаем production installer в SMALL-VPS режиме...\n\n'
exec bash "${INSTALLER}"
