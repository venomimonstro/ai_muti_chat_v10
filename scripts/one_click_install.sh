#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${AIWS_REPO_URL:-https://github.com/venomimonstro/ai_muti_chat_v10.git}"
BRANCH="${AIWS_BRANCH:-main}"
TARGET_DIR="${AIWS_INSTALL_DIR:-/opt/ai-workspace}"
SWAP_FILE="${AIWS_SWAP_FILE:-/swapfile-aiws}"
SWAP_SIZE_GB="${AIWS_SWAP_SIZE_GB:-4}"

fail(){ printf 'Ошибка bootstrap-установки: %s\n' "$1" >&2; exit 1; }
trap 'printf "Bootstrap остановлен на строке %s. Уже скачанные данные не удалялись.\n" "$LINENO" >&2' ERR

[[ "${EUID}" -eq 0 ]] || fail "запустите команду через sudo"
command -v apt-get >/dev/null 2>&1 || fail "поддерживаются Ubuntu/Debian с apt"
[[ "${BRANCH}" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "некорректное имя ветки"
[[ "${TARGET_DIR}" == /* ]] || fail "AIWS_INSTALL_DIR должен быть абсолютным путём"
[[ "${SWAP_SIZE_GB}" =~ ^[1-9][0-9]*$ ]] || fail "AIWS_SWAP_SIZE_GB должен быть целым числом"

memory_preflight(){
  local mem_kb swap_kb free_kb need_swap=false
  mem_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  swap_kb="$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  free_kb="$(df -Pk / | awk 'NR==2{print $4}')"

  printf 'Память сервера: RAM %s МБ, swap %s МБ.\n' "$((mem_kb/1024))" "$((swap_kb/1024))"
  (( mem_kb >= 900000 )) || fail "слишком мало RAM ($((mem_kb/1024)) МБ). Нужен сервер минимум около 1 ГБ RAM, рекомендуется 4 ГБ+"

  # Docker/BuildKit/Next.js могут кратковременно съесть существенно больше RAM.
  # На VPS до 4 ГБ автоматически создаём swap ДО apt/docker/build, чтобы OOM-killer
  # не убивал bootstrap посреди установки.
  if (( mem_kb < 4000000 && swap_kb < 2000000 )); then
    need_swap=true
  fi
  if [[ "${need_swap}" == true ]]; then
    local required_kb=$((SWAP_SIZE_GB * 1024 * 1024))
    (( free_kb > required_kb + 1048576 )) || fail "недостаточно диска для временного swap ${SWAP_SIZE_GB} ГБ"
    if swapon --show=NAME --noheadings 2>/dev/null | grep -Fxq "${SWAP_FILE}"; then
      printf 'Swap %s уже активен.\n' "${SWAP_FILE}"
    else
      printf 'Создаём swap %s ГБ перед установкой Docker...\n' "${SWAP_SIZE_GB}"
      if [[ ! -f "${SWAP_FILE}" ]]; then
        if command -v fallocate >/dev/null 2>&1; then
          fallocate -l "${SWAP_SIZE_GB}G" "${SWAP_FILE}" || dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="$((SWAP_SIZE_GB*1024))" status=progress
        else
          dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="$((SWAP_SIZE_GB*1024))" status=progress
        fi
      fi
      chmod 600 "${SWAP_FILE}"
      mkswap "${SWAP_FILE}" >/dev/null
      swapon "${SWAP_FILE}"
      if ! grep -Fq "${SWAP_FILE} none swap sw 0 0" /etc/fstab; then
        printf '%s none swap sw 0 0\n' "${SWAP_FILE}" >> /etc/fstab
      fi
      printf 'Swap активирован.\n'
    fi
  fi
}

memory_preflight

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git openssl

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

cd "${TARGET_DIR}"
printf '\nИсходный код установлен в %s. Запускаем мастер production-настройки.\n\n' "${TARGET_DIR}"

if [[ "${AIWS_NONINTERACTIVE:-false}" != "true" && -r /dev/tty ]]; then
  exec bash "${TARGET_DIR}/install.sh" </dev/tty
fi
exec bash "${TARGET_DIR}/install.sh"
