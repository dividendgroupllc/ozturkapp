#!/usr/bin/env bash
#
# ozturkapp — serverga deploy skripti
#
# Ketma-ketlik:
#   git fetch/reset -> (kerak bo'lsa) python paketlar -> bench build
#   -> bench migrate -> clear-cache -> bench restart
#
# Ishlatish:
#   ./deploy.sh            # origin/<BRANCH> dagi oxirgi kodni deploy qiladi
#   ./deploy.sh --force    # yangi commit bo'lmasa ham to'liq bajaradi
#
# Sozlamalar: deploy/deploy.env  (deploy.env.example dan nusxa oling)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DEPLOY_CONFIG="${DEPLOY_CONFIG:-$SCRIPT_DIR/deploy.env}"
# /tmp ga nusxa olingandan keyin ham xabarlarda asl yo'l ko'rinishi uchun
export DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")}"

# git reset --hard shu skriptning o'zini ham qayta yozadi. Bash faylni bo'lak-bo'lak
# o'qigani uchun ishlab turgan skript buzilib qolmasligi kerak — /tmp ga nusxa olib,
# o'shandan davom etamiz.
if [[ "${DEPLOY_RUNNING_FROM_COPY:-0}" != "1" ]]; then
	_self="$(mktemp /tmp/ozturkapp-deploy-XXXXXX.sh)"
	cat "${BASH_SOURCE[0]}" >"$_self"
	DEPLOY_RUNNING_FROM_COPY=1 exec bash "$_self" "$@"
fi
trap 'rm -f -- "$0"' EXIT

FORCE=0
for arg in "$@"; do
	case "$arg" in
	--force | -f) FORCE=1 ;;
	*)
		echo "Noma'lum argument: $arg" >&2
		exit 2
		;;
	esac
done

if [[ ! -f "$DEPLOY_CONFIG" ]]; then
	echo "Config topilmadi: $DEPLOY_CONFIG" >&2
	echo "deploy/deploy.env.example dan nusxa olib to'ldiring." >&2
	exit 1
fi
# shellcheck disable=SC1090
set -a
source "$DEPLOY_CONFIG"
set +a

: "${BENCH_PATH:?deploy.env da BENCH_PATH ko'rsatilmagan}"
: "${SITES:?deploy.env da SITES ko'rsatilmagan}"
APP_NAME="${APP_NAME:-ozturkapp}"
BRANCH="${BRANCH:-main}"
BENCH_BIN="${BENCH_BIN:-bench}"
BUILD_ARGS="${BUILD_ARGS:---app $APP_NAME}"
RESTART_CMD="${RESTART_CMD:-$BENCH_BIN restart}"
DO_RESTART="${DO_RESTART:-1}"
LOG_FILE="${LOG_FILE:-$BENCH_PATH/logs/deploy-$APP_NAME.log}"
LOCK_FILE="${LOCK_FILE:-/tmp/deploy-$APP_NAME.lock}"
LOCK_WAIT="${LOCK_WAIT:-900}"

APP_PATH="$BENCH_PATH/apps/$APP_NAME"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
fail() {
	log "XATO: $1"
	log "Kod hozir $(git -C "$APP_PATH" rev-parse --short HEAD 2>/dev/null || echo '?') holatida."
	log "Orqaga qaytarish: git -C $APP_PATH reset --hard ${OLD_REV:-<eski-commit>} && $DEPLOY_SCRIPT --force"
	exit 1
}

# Bir vaqtda ikkita deploy ketmasligi uchun. Ketma-ket push bo'lsa navbatda kutadi.
exec 200>"$LOCK_FILE"
if ! flock -w "$LOCK_WAIT" 200; then
	log "Oldingi deploy ${LOCK_WAIT}s ichida tugamadi — bu deploy bekor qilindi."
	exit 1
fi

log "=============================================================="
log "Deploy boshlandi: $APP_NAME @ $BRANCH  (bench: $BENCH_PATH)"

[[ -d "$APP_PATH/.git" ]] || fail "$APP_PATH git repo emas"
[[ -O "$BENCH_PATH" ]] || log "OGOHLANTIRISH: $BENCH_PATH boshqa foydalanuvchiga tegishli — skriptni bench egasi nomidan ishlating."

cd "$APP_PATH"

# ---------------------------------------------------------------- 1. kodni tortish
OLD_REV="$(git rev-parse HEAD)"
log "Hozirgi commit: $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"

git fetch --prune --quiet origin || fail "git fetch bajarilmadi (deploy key / SSH ni tekshiring)"
git checkout -q "$BRANCH" 2>/dev/null || git checkout -q -B "$BRANCH" "origin/$BRANCH"
git reset --hard --quiet "origin/$BRANCH" || fail "git reset bajarilmadi"

NEW_REV="$(git rev-parse HEAD)"
log "Yangi commit:   $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"

if [[ "$OLD_REV" == "$NEW_REV" && "$FORCE" != "1" ]]; then
	log "O'zgarish yo'q — deploy o'tkazib yuborildi. To'liq bajarish uchun: $DEPLOY_SCRIPT --force"
	exit 0
fi

CHANGED="$(git diff --name-only "$OLD_REV" "$NEW_REV" 2>/dev/null || echo "")"

cd "$BENCH_PATH"

# ---------------------------------------------------------- 2. python bog'liqliklari
if [[ "$FORCE" == "1" ]] || grep -qE '^(pyproject\.toml|requirements\.txt|setup\.py)$' <<<"$CHANGED"; then
	log "--> Python paketlari yangilanmoqda (pyproject.toml o'zgargan)"
	"$BENCH_PATH/env/bin/pip" install --quiet --upgrade -e "$APP_PATH" || fail "pip install bajarilmadi"
fi

# ------------------------------------------------------------------- 3. assets build
log "--> bench build $BUILD_ARGS"
# shellcheck disable=SC2086
$BENCH_BIN build $BUILD_ARGS || fail "bench build bajarilmadi"

# ------------------------------------------------ 4. migrate + cache (har bir sayt uchun)
for site in $SITES; do
	log "--> [$site] bench migrate"
	$BENCH_BIN --site "$site" migrate || fail "[$site] migrate bajarilmadi — restart qilinmadi"

	log "--> [$site] clear-cache"
	$BENCH_BIN --site "$site" clear-cache || fail "[$site] clear-cache bajarilmadi"
	$BENCH_BIN --site "$site" clear-website-cache || fail "[$site] clear-website-cache bajarilmadi"
done

# ----------------------------------------------------------------------- 5. restart
if [[ "$DO_RESTART" == "1" ]]; then
	log "--> $RESTART_CMD"
	eval "$RESTART_CMD" || fail "restart bajarilmadi (sudoers sozlamasini tekshiring)"
else
	log "--> restart o'tkazib yuborildi (DO_RESTART=0)"
fi

log "Deploy muvaffaqiyatli tugadi: $(git -C "$APP_PATH" rev-parse --short HEAD)"
log "=============================================================="
