#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${AIWS_REPO_URL:-https://github.com/venomimonstro/ai_muti_chat_v10.git}"
BRANCH="${AIWS_BRANCH:-main}"
TARGET_DIR="${AIWS_INSTALL_DIR:-/opt/ai-workspace}"
SWAP_FILE="${AIWS_SWAP_FILE:-/swapfile-aiws}"
SWAP_SIZE_GB="${AIWS_SWAP_SIZE_GB:-}"
LOW_DISK_MODE=false

fail(){ printf 'Ошибка bootstrap-установки: %s\n' "$1" >&2; exit 1; }
trap 'printf "Bootstrap остановлен на строке %s. Уже скачанные данные не удалялись.\n" "$LINENO" >&2' ERR

[[ "${EUID}" -eq 0 ]] || fail "запустите команду через sudo"
command -v apt-get >/dev/null 2>&1 || fail "поддерживаются Ubuntu/Debian с apt"
[[ "${BRANCH}" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "некорректное имя ветки"
[[ "${TARGET_DIR}" == /* ]] || fail "AIWS_INSTALL_DIR должен быть абсолютным путём"

create_swap(){
  local size_gb="$1"
  printf 'Создаём swap %s ГБ: %s\n' "${size_gb}" "${SWAP_FILE}"
  rm -f "${SWAP_FILE}"
  if command -v fallocate >/dev/null 2>&1; then
    fallocate -l "${size_gb}G" "${SWAP_FILE}" || dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="$((size_gb*1024))" status=progress
  else
    dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="$((size_gb*1024))" status=progress
  fi
  chmod 600 "${SWAP_FILE}"
  mkswap "${SWAP_FILE}" >/dev/null
  swapon "${SWAP_FILE}"
  if ! grep -Fq "${SWAP_FILE} none swap sw 0 0" /etc/fstab; then
    printf '%s none swap sw 0 0\n' "${SWAP_FILE}" >> /etc/fstab
  fi
}

memory_preflight(){
  local mem_kb swap_kb free_kb total_kb current_managed_swap_kb=0
  mem_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  swap_kb="$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  free_kb="$(df -Pk / | awk 'NR==2{print $4}')"
  total_kb="$(df -Pk / | awk 'NR==2{print $2}')"

  printf 'Сервер: RAM %s МБ, swap %s МБ, свободно на диске %.1f ГБ.\n' "$((mem_kb/1024))" "$((swap_kb/1024))" "$(awk -v kb="$free_kb" 'BEGIN{printf "%.1f", kb/1024/1024}')"
  (( mem_kb >= 850000 )) || fail "слишком мало RAM ($((mem_kb/1024)) МБ). Нужен сервер примерно от 1 ГБ RAM"

  if [[ -z "${SWAP_SIZE_GB}" ]]; then
    if (( total_kb < 16777216 )); then SWAP_SIZE_GB=2; else SWAP_SIZE_GB=4; fi
  fi
  [[ "${SWAP_SIZE_GB}" =~ ^[1-9][0-9]*$ ]] || fail "AIWS_SWAP_SIZE_GB должен быть целым числом"

  # На маленьком VPS 4 ГБ swap может занять половину системного диска. Если этот
  # bootstrap ранее создал /swapfile-aiws 4 ГБ, а диск тесный, уменьшаем только
  # наш управляемый swap до 2 ГБ. Чужие swap-файлы не трогаем.
  if [[ -f "${SWAP_FILE}" ]]; then
    current_managed_swap_kb="$(du -k "${SWAP_FILE}" 2>/dev/null | awk '{print $1}' || echo 0)"
  fi
  if (( mem_kb < 1600000 && free_kb < 6291456 && current_managed_swap_kb > 2621440 )); then
    printf 'LOW-DISK: уменьшаем управляемый swap с %.1f ГБ до 2 ГБ, чтобы освободить место для Docker-образов.\n' "$(awk -v kb="$current_managed_swap_kb" 'BEGIN{printf "%.1f", kb/1024/1024}')"
    swapoff "${SWAP_FILE}" 2>/dev/null || true
    create_swap 2
    SWAP_SIZE_GB=2
    swap_kb="$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    free_kb="$(df -Pk / | awk 'NR==2{print $4}')"
  fi

  if (( mem_kb < 4000000 && swap_kb < 1800000 )); then
    local required_kb=$((SWAP_SIZE_GB * 1024 * 1024))
    (( free_kb > required_kb + 1572864 )) || fail "недостаточно диска для swap ${SWAP_SIZE_GB} ГБ и установки. Нужен больший диск"
    create_swap "${SWAP_SIZE_GB}"
    swap_kb="$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    free_kb="$(df -Pk / | awk 'NR==2{print $4}')"
  fi

  if (( free_kb < 6291456 )); then
    LOW_DISK_MODE=true
    printf 'LOW-DISK режим: свободно %.1f ГБ. Используем компактную сборку и очистку временного cache.\n' "$(awk -v kb="$free_kb" 'BEGIN{printf "%.1f", kb/1024/1024}')"
  fi
  (( free_kb >= 4194304 )) || fail "после настройки swap осталось меньше 4 ГБ свободного диска. Этого недостаточно даже для компактной production-сборки"
}

memory_preflight

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git openssl
apt-get clean
rm -rf /var/lib/apt/lists/*

git --version >/dev/null 2>&1 || fail "git не установлен"

if [[ -e "${TARGET_DIR}/.installed" ]]; then
  fail "AI Workspace уже установлен в ${TARGET_DIR}. Для обновления используйте sudo bash ${TARGET_DIR}/scripts/update.sh"
fi

mkdir -p "$(dirname "${TARGET_DIR}")"

if [[ -d "${TARGET_DIR}/.git" ]]; then
  printf 'Найден незавершённый checkout %s, синхронизируем %s...\n' "${TARGET_DIR}" "${BRANCH}"
  git -C "${TARGET_DIR}" remote get-url origin >/dev/null 2>&1 || fail "у существующего checkout отсутствует origin"
  git -C "${TARGET_DIR}" fetch --depth 1 origin "${BRANCH}"
  git -C "${TARGET_DIR}" checkout -B "${BRANCH}" "origin/${BRANCH}"
  git -C "${TARGET_DIR}" reset --hard "origin/${BRANCH}"
elif [[ -e "${TARGET_DIR}" ]]; then
  if [[ -d "${TARGET_DIR}" && -z "$(find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    rmdir "${TARGET_DIR}"
    git clone --depth 1 --single-branch --branch "${BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
  else
    fail "${TARGET_DIR} существует, но не является checkout проекта. Переместите/удалите его вручную"
  fi
else
  git clone --depth 1 --single-branch --branch "${BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
fi

[[ -f "${TARGET_DIR}/install.sh" ]] || fail "install.sh отсутствует после checkout"
[[ -f "${TARGET_DIR}/docker-compose.prod.yml" ]] || fail "docker-compose.prod.yml отсутствует после checkout"
bash -n "${TARGET_DIR}/install.sh" || fail "install.sh содержит синтаксическую ошибку"

# На маленьком диске запускаем временную копию installer с более реалистичным
# preflight. Сам tracked install.sh не меняется; production-конфигурация остаётся той же.
INSTALL_ENTRY="${TARGET_DIR}/install.sh"
if [[ "${LOW_DISK_MODE}" == true ]]; then
  INSTALL_ENTRY="${TARGET_DIR}/.install-low-disk-runtime.sh"
  sed \
    -e 's/if (( disk_kb < 10000000 )); then/if (( disk_kb < 4194304 )); then/' \
    -e 's/нужно минимум около 10 ГБ свободного диска/нужно минимум около 4 ГБ свободного диска в LOW-DISK режиме/' \
    "${TARGET_DIR}/install.sh" >"${INSTALL_ENTRY}"
  chmod 700 "${INSTALL_ENTRY}"
fi

# На незавершённой установке без пользовательских контейнеров безопасно очищаем
# только build cache/dangling layers. Volumes, базы и именованные images не удаляем.
if [[ "${LOW_DISK_MODE}" == true ]] && command -v docker >/dev/null 2>&1; then
  if [[ -z "$(docker ps -q 2>/dev/null)" ]]; then
    docker builder prune -af >/dev/null 2>&1 || true
    docker image prune -f >/dev/null 2>&1 || true
  fi
fi

cd "${TARGET_DIR}"
printf '\nИсходный код установлен в %s. Запускаем мастер production-настройки.\n\n' "${TARGET_DIR}"

if [[ "${AIWS_NONINTERACTIVE:-false}" != "true" && -r /dev/tty ]]; then
  exec bash "${INSTALL_ENTRY}" </dev/tty
fi
exec bash "${INSTALL_ENTRY}"
