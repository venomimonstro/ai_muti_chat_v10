#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${AIWS_REPO_URL:-https://github.com/venomimonstro/ai_muti_chat_v10.git}"
BRANCH="${AIWS_BRANCH:-main}"
TARGET_DIR="${AIWS_INSTALL_DIR:-/opt/ai-workspace}"

fail(){ printf 'Ошибка bootstrap-установки: %s\n' "$1" >&2; exit 1; }
trap 'printf "Bootstrap остановлен на строке %s. Уже скачанные данные не удалялись.\n" "$LINENO" >&2' ERR

[[ "${EUID}" -eq 0 ]] || fail "запустите команду через sudo"
command -v apt-get >/dev/null 2>&1 || fail "поддерживаются Ubuntu/Debian с apt"
[[ "${BRANCH}" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "некорректное имя ветки"
[[ "${TARGET_DIR}" == /* ]] || fail "AIWS_INSTALL_DIR должен быть абсолютным путём"

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
  # Не удаляем неизвестные пользовательские данные автоматически.
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

# При запуске через curl | sudo bash сохраняем интерактивный мастер через /dev/tty.
if [[ "${AIWS_NONINTERACTIVE:-false}" != "true" && -r /dev/tty ]]; then
  exec bash "${TARGET_DIR}/install.sh" </dev/tty
fi
exec bash "${TARGET_DIR}/install.sh"
