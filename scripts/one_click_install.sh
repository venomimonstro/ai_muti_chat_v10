#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${AIWS_REPO_URL:-https://github.com/venomimonstro/ai_muti_chat_v10.git}"
BRANCH="${AIWS_BRANCH:-main}"
TARGET_DIR="${AIWS_INSTALL_DIR:-/opt/ai-workspace}"

fail(){ printf 'Ошибка bootstrap-установки: %s\n' "$1" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || fail "запустите команду через sudo"
command -v apt-get >/dev/null 2>&1 || fail "поддерживаются Ubuntu/Debian с apt"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git openssl

if [[ -e "${TARGET_DIR}/.installed" ]]; then
  fail "AI Workspace уже установлен в ${TARGET_DIR}. Для обновления используйте scripts/update.sh"
fi

if [[ -d "${TARGET_DIR}/.git" ]]; then
  printf 'Найден незавершённый checkout %s, обновляем файлы...\n' "${TARGET_DIR}"
  git -C "${TARGET_DIR}" fetch --depth 1 origin "${BRANCH}"
  git -C "${TARGET_DIR}" checkout -f "${BRANCH}"
  git -C "${TARGET_DIR}" reset --hard "origin/${BRANCH}"
elif [[ -e "${TARGET_DIR}" ]]; then
  fail "${TARGET_DIR} существует, но не является checkout проекта"
else
  mkdir -p "$(dirname "${TARGET_DIR}")"
  git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
fi

cd "${TARGET_DIR}"
printf '\nИсходный код установлен в %s. Запускаем мастер настройки.\n\n' "${TARGET_DIR}"

if [[ -r /dev/tty ]]; then
  exec bash "${TARGET_DIR}/install.sh" </dev/tty
fi
exec bash "${TARGET_DIR}/install.sh"
