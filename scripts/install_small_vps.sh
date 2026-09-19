#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_INSTALLER="${PROJECT_DIR}/install.sh"
RUNTIME_INSTALLER="${PROJECT_DIR}/.install-small-vps-runtime.sh"
VERIFY_SCRIPT="${PROJECT_DIR}/scripts/verify_installation.sh"

fail(){ printf 'Ошибка small-VPS установки: %s\n' "$1" >&2; exit 1; }
[[ "${EUID}" -eq 0 ]] || fail "запустите через sudo"
[[ -f "${BASE_INSTALLER}" ]] || fail "install.sh не найден"
[[ -f "${VERIFY_SCRIPT}" ]] || fail "scripts/verify_installation.sh не найден"
command -v python3 >/dev/null 2>&1 || fail "python3 не установлен; запустите one_click_install.sh"

mem_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
swap_kb="$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
disk_kb="$(df -Pk "${PROJECT_DIR}" | awk 'NR==2{print $4}')"

printf 'SMALL-VPS режим: RAM %s МБ, swap %s МБ, свободный диск %.1f ГБ.\n' \
  "$((mem_kb/1024))" "$((swap_kb/1024))" "$(awk -v k="$disk_kb" 'BEGIN{printf "%.1f", k/1024/1024}')"

(( mem_kb >= 1700000 )) || fail "для этого профиля нужно около 2 ГБ RAM"
(( swap_kb >= 1500000 )) || fail "нужен swap минимум около 1.5 ГБ; запустите one_click_install.sh, он настроит swap"

if command -v sysctl >/dev/null 2>&1; then
  printf 'vm.overcommit_memory = 1\n' >/etc/sysctl.d/99-ai-workspace.conf
  sysctl -w vm.overcommit_memory=1 >/dev/null
fi

export UVICORN_WORKERS=1
export CELERY_CONCURRENCY=1
export COMPOSE_PARALLEL_LIMIT=1

apt-get clean >/dev/null 2>&1 || true
if command -v docker >/dev/null 2>&1; then
  if ! docker ps -q 2>/dev/null | grep -q .; then
    docker builder prune -af >/dev/null 2>&1 || true
    docker image prune -f >/dev/null 2>&1 || true
  fi
fi

disk_kb="$(df -Pk "${PROJECT_DIR}" | awk 'NR==2{print $4}')"
(( disk_kb >= 3200000 )) || fail "после очистки нужно хотя бы около 3.2 ГБ свободного диска; сейчас $(awk -v k="$disk_kb" 'BEGIN{printf "%.1f", k/1024/1024}') ГБ"

cp "${BASE_INSTALLER}" "${RUNTIME_INSTALLER}"
chmod 700 "${RUNTIME_INSTALLER}"

python3 - "${RUNTIME_INSTALLER}" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = '''  if (( disk_kb < 10000000 )); then\n    fail "нужно минимум около 10 ГБ свободного диска"\n  fi'''
new = '''  if (( disk_kb < 3200000 )); then\n    fail "для SMALL-VPS режима нужно минимум около 3.2 ГБ свободного диска"\n  elif (( disk_kb < 6000000 )); then\n    printf 'SMALL-VPS/LOW-DISK режим: свободного диска меньше 6 ГБ; сборка будет последовательной.\\n' >&2\n    LOW_MEMORY_MODE=true\n    UVICORN_WORKERS=1\n    CELERY_CONCURRENCY=1\n    export COMPOSE_PARALLEL_LIMIT=1\n  fi'''
if old not in s:
    raise SystemExit('Не найден ожидаемый disk-check в install.sh')
s = s.replace(old, new, 1)
p.write_text(s)
PY

bash -n "${RUNTIME_INSTALLER}" || fail "runtime install.sh повреждён после адаптации"
printf 'Запускаем production installer в SMALL-VPS режиме...\n\n'
bash "${RUNTIME_INSTALLER}"

printf '\nЗапускаем усиленную итоговую проверку установки...\n'
if ! bash "${VERIFY_SCRIPT}"; then
  rm -f "${PROJECT_DIR}/.installed"
  fail "итоговая проверка SMALL-VPS установки не прошла; маркер .installed снят"
fi

printf 'SMALL-VPS установка полностью проверена.\n'
