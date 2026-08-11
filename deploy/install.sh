#!/usr/bin/env bash
#
# ozturkapp deploy webhook — serverda birinchi marta sozlash uchun yordamchi.
#
# Serverda bench egasi (odatda "frappe") nomidan ishga tushiring:
#   cd ~/frappe-bench/apps/ozturkapp && bash deploy/install.sh
#
# Bu skript:
#   1. deploy/deploy.env ni yaratadi (yo'llarni avtomatik topib, maxfiy kalit generatsiya qilib)
#   2. systemd unit faylini serverning yo'llariga moslab /tmp ga tayyorlaydi
#   3. Qolgan qadamlarni (sudo buyruqlari, GitHub sozlamalari) ekranga chiqaradi
#
# Hech narsani sudo bilan o'zi bajarmaydi — buyruqlarni siz ko'rib chiqib ishlatasiz.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PATH="$(dirname "$SCRIPT_DIR")"
BENCH_PATH="$(dirname "$(dirname "$APP_PATH")")"
APP_NAME="$(basename "$APP_PATH")"
ENV_FILE="$SCRIPT_DIR/deploy.env"
UNIT_NAME="ozturkapp-webhook.service"
RENDERED_UNIT="/tmp/$UNIT_NAME"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

[[ -d "$BENCH_PATH/sites" ]] || {
	echo "Bench topilmadi: $BENCH_PATH" >&2
	echo "Skriptni bench ichidagi apps/$APP_NAME/deploy/ dan ishga tushiring." >&2
	exit 1
}

bold "Aniqlangan yo'llar"
echo "  bench:  $BENCH_PATH"
echo "  app:    $APP_PATH"
echo "  user:   $(id -un)"
echo

# ------------------------------------------------------------------ 1. deploy.env
if [[ -f "$ENV_FILE" ]]; then
	echo "deploy.env allaqachon mavjud — o'zgartirilmadi: $ENV_FILE"
else
	SECRET="$(openssl rand -hex 32 2>/dev/null || head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')"

	# ozturkapp o'rnatilgan saytlarni topamiz
	DETECTED_SITES=""
	for site_dir in "$BENCH_PATH"/sites/*/; do
		site="$(basename "$site_dir")"
		[[ -f "$site_dir/site_config.json" ]] || continue
		if grep -qx "$APP_NAME" "$site_dir/apps.txt" 2>/dev/null; then
			DETECTED_SITES="$DETECTED_SITES $site"
		fi
	done
	DETECTED_SITES="$(echo "$DETECTED_SITES" | xargs || true)"

	if [[ -z "$DETECTED_SITES" ]]; then
		echo "OGOHLANTIRISH: $APP_NAME o'rnatilgan sayt topilmadi — SITES ni qo'lda yozing."
		DETECTED_SITES="SAYTNI_YOZING"
	fi

	# Qiymatlar qo'shtirnoq ichida yoziladi — fayl bash tomonidan source qilinadi.
	sed \
		-e "s|^BENCH_PATH=.*|BENCH_PATH=\"$BENCH_PATH\"|" \
		-e "s|^SITES=.*|SITES=\"$DETECTED_SITES\"|" \
		-e "s|^WEBHOOK_SECRET=.*|WEBHOOK_SECRET=\"$SECRET\"|" \
		-e "s|^APP_NAME=.*|APP_NAME=\"$APP_NAME\"|" \
		"$SCRIPT_DIR/deploy.env.example" >"$ENV_FILE"
	chmod 600 "$ENV_FILE"

	bold "deploy.env yaratildi: $ENV_FILE"
	echo "  SITES=$DETECTED_SITES"
	echo "  WEBHOOK_SECRET=$SECRET"
	echo "  (SITES to'g'ri ekanini tekshiring)"
fi
echo

# ------------------------------------------------------------------ 2. systemd unit
sed \
	-e "s|^User=.*|User=$(id -un)|" \
	-e "s|^Group=.*|Group=$(id -gn)|" \
	-e "s|^WorkingDirectory=.*|WorkingDirectory=$APP_PATH|" \
	-e "s|^Environment=DEPLOY_CONFIG=.*|Environment=DEPLOY_CONFIG=$ENV_FILE|" \
	-e "s|^Environment=PATH=.*|Environment=PATH=$BENCH_PATH/env/bin:/usr/local/bin:/usr/bin:/bin|" \
	-e "s|^ExecStart=.*|ExecStart=$(command -v python3) $SCRIPT_DIR/webhook_server.py|" \
	"$SCRIPT_DIR/$UNIT_NAME" >"$RENDERED_UNIT"

bold "systemd unit tayyorlandi: $RENDERED_UNIT"
echo

WEBHOOK_PORT="$(grep -E '^WEBHOOK_PORT=' "$ENV_FILE" | cut -d= -f2 | xargs || echo 9987)"
WEBHOOK_SECRET="$(grep -E '^WEBHOOK_SECRET=' "$ENV_FILE" | cut -d= -f2 | xargs || echo '')"
WEBHOOK_PATH="$(grep -E '^WEBHOOK_PATH=' "$ENV_FILE" | cut -d= -f2 | xargs || echo /hook)"

cat <<EOF
$(bold "Keyingi qadamlar")

1) bench restart uchun parolsiz sudo (agar hali sozlanmagan bo'lsa):
     sudo bench setup sudoers $(id -un)

2) Xizmatni o'rnatish:
     sudo cp $RENDERED_UNIT /etc/systemd/system/$UNIT_NAME
     sudo systemctl daemon-reload
     sudo systemctl enable --now ${UNIT_NAME%.service}
     systemctl status ${UNIT_NAME%.service}

3) Portni ochish (nginx orqali proxy qilmasangiz):
     deploy.env da WEBHOOK_BIND=0.0.0.0 qiling, so'ng:
     sudo ufw allow $WEBHOOK_PORT/tcp

4) GitHub: repo > Settings > Webhooks > Add webhook
     Payload URL:  http://<SERVER-IP>:$WEBHOOK_PORT$WEBHOOK_PATH
     Content type: application/json
     Secret:       $WEBHOOK_SECRET
     Events:       Just the push event

5) Tekshirish:
     bash $SCRIPT_DIR/deploy.sh --force
     journalctl -u ${UNIT_NAME%.service} -f

Batafsil: $SCRIPT_DIR/README.md
EOF
