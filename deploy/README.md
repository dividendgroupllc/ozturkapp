# ozturkapp — avtomatik deploy (CI/CD)

`main` branchga push qilinganda server kodni o'zi tortib oladi va deploy qiladi.

```
git push origin main
        │
        ▼
GitHub webhook  ──HMAC-SHA256 imzo──▶  server:9987/hook   (webhook_server.py)
                                              │
                                              ▼
                                        deploy.sh
                                              │
        git fetch + reset --hard  ─▶  pip (kerak bo'lsa)  ─▶  bench build
        ─▶  bench migrate  ─▶  clear-cache  ─▶  bench restart
```

## Fayllar

| Fayl | Vazifasi |
|---|---|
| `deploy.sh` | Asosiy deploy skripti. Qo'lda ham ishlatiladi. |
| `webhook_server.py` | GitHub push hodisasini kutadigan HTTP server (faqat stdlib). |
| `ozturkapp-webhook.service` | systemd unit — webhook serverini doimiy ishlatib turadi. |
| `deploy.env.example` | Sozlamalar namunasi. Serverda `deploy.env` ga nusxa olinadi. |
| `install.sh` | Birinchi o'rnatishni osonlashtiruvchi yordamchi. |

`deploy.env` `.gitignore` da — unda maxfiy kalit bor va u git ga tushmasligi kerak.

---

## Serverda o'rnatish (bir marta)

Hamma buyruqlar **bench egasi** (odatda `frappe`) nomidan bajariladi.

### 1. Server GitHub'dan kod tortа olishini tekshiring

```bash
cd ~/frappe-bench/apps/ozturkapp
git remote -v          # git@github.com:dividendgroupllc/ozturkapp.git bo'lishi kerak
git fetch origin       # xatosiz o'tishi kerak
```

Agar `Permission denied` chiqsa, serverga deploy key qo'shing:

```bash
ssh-keygen -t ed25519 -C "ozturkapp-deploy" -f ~/.ssh/ozturkapp_deploy -N ""
cat ~/.ssh/ozturkapp_deploy.pub
```

Chiqqan kalitni GitHub'da: **repo → Settings → Deploy keys → Add deploy key**
(*Allow write access* kerak emas). So'ng `~/.ssh/config` ga:

```
Host github.com
    IdentityFile ~/.ssh/ozturkapp_deploy
    IdentitiesOnly yes
```

### 2. Sozlamalarni yarating

```bash
cd ~/frappe-bench/apps/ozturkapp
bash deploy/install.sh
```

Skript `deploy/deploy.env` ni yaratadi: bench yo'lini topadi, `ozturkapp` o'rnatilgan
saytlarni aniqlaydi va webhook uchun maxfiy kalit generatsiya qiladi. Faylni ochib
`SITES` to'g'ri ekanini tekshiring.

### 3. `bench restart` uchun parolsiz sudo

```bash
sudo bench setup sudoers frappe     # frappe — bench egasining nomi
```

Buni qilmasangiz deploy oxirida restart qadami xato beradi (migrate esa bajarilgan
bo'ladi). Muqobil variant: `deploy.env` da `DO_RESTART="0"` qilib qo'ying.

### 4. Webhook xizmatini o'rnating

`install.sh` tayyorlab bergan unit faylni joylashtiring:

```bash
sudo cp /tmp/ozturkapp-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ozturkapp-webhook
systemctl status ozturkapp-webhook
```

### 5. Portni oching

**A variant — oddiy (HTTP, tavsiya etiladi boshlanishiga).**
Imzo HMAC-SHA256 bilan tekshirilgani uchun maxfiy kalit tarmoqqa hech qachon
uzatilmaydi; HTTP'da faqat commit ma'lumotlari ochiq ketadi.

`deploy.env` da `WEBHOOK_BIND="0.0.0.0"` qiling, so'ng:

```bash
sudo systemctl restart ozturkapp-webhook
sudo ufw allow 9987/tcp
```

**B variant — nginx orqali HTTPS.** `WEBHOOK_BIND="127.0.0.1"` qoldiring va
`/etc/nginx/conf.d/frappe-bench.conf` ichidagi sayt `server { ... }` blokiga
qo'shing:

```nginx
location /deploy-hook {
    proxy_pass http://127.0.0.1:9987/hook;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

> Diqqat: bu faylni `bench setup nginx` qayta yozadi. Shundan keyin blokni qayta
> qo'shish kerak bo'ladi.

### 6. GitHub webhook'ni sozlang

**repo → Settings → Webhooks → Add webhook**

| Maydon | Qiymat |
|---|---|
| Payload URL | `http://<SERVER-IP>:9987/hook` (yoki `https://ozturk.erpcontrol.uz/deploy-hook`) |
| Content type | `application/json` |
| Secret | `deploy.env` dagi `WEBHOOK_SECRET` qiymati |
| SSL verification | yoqilgan (HTTPS ishlatsangiz) |
| Events | **Just the push event** |

Saqlagach GitHub darhol `ping` yuboradi — **Recent Deliveries** da yashil belgi va
`pong` javobi ko'rinishi kerak.

---

## Tekshirish

```bash
# 1. Qo'lda to'liq deploy
bash ~/frappe-bench/apps/ozturkapp/deploy/deploy.sh --force

# 2. Xizmat sog'ligi
curl http://127.0.0.1:9987/health          # -> ok

# 3. Haqiqiy push bilan
#    lokal kompyuterdan: git push origin main
journalctl -u ozturkapp-webhook -f
```

---

## Kundalik ishlatish

Odatiy holda hech narsa qilish shart emas — `main` ga push qilsangiz kifoya.

```bash
git push origin main
```

Qo'shimcha buyruqlar:

```bash
deploy/deploy.sh              # oxirgi kodni tortib deploy qiladi
deploy/deploy.sh --force      # yangi commit bo'lmasa ham to'liq bajaradi
```

---

## Jurnallar va diagnostika

```bash
# Webhook qabul qilinganini ko'rish
journalctl -u ozturkapp-webhook -f

# Deploy jurnali (to'liq chiqish, migrate xatolari bilan)
tail -f ~/frappe-bench/logs/deploy-ozturkapp.log
```

| Muammo | Sabab / yechim |
|---|---|
| GitHub'da `401 invalid signature` | `deploy.env` dagi `WEBHOOK_SECRET` GitHub'dagi Secret bilan bir xil emas |
| GitHub'da `We couldn't deliver` | Port yopiq — `sudo ufw allow 9987/tcp`, `WEBHOOK_BIND="0.0.0.0"` |
| `git fetch bajarilmadi` | Deploy key yo'q yoki noto'g'ri (1-qadam) |
| `restart bajarilmadi` | `sudo bench setup sudoers frappe` bajarilmagan |
| Webhook keladi, lekin deploy yo'q | Push `main` ga emas, boshqa branchga qilingan |
| `deploy.sh` yangilandi, lekin xizmat eski holatda | `webhook_server.py` o'zgargan bo'lsa: `sudo systemctl restart ozturkapp-webhook` |

---

## Orqaga qaytarish (rollback)

Deploy xato bersa skript **restart qilmaydi** va oxirgi ishlagan commit'ni jurnalda
ko'rsatadi:

```bash
cd ~/frappe-bench/apps/ozturkapp
git reset --hard <eski-commit>
bash deploy/deploy.sh --force
```

> Migrate bajarilib bo'lgan bo'lsa, kodni qaytarish bazadagi o'zgarishlarni
> qaytarmaydi. Xavfli migratsiyalardan oldin `bench --site <sayt> backup` oling.

---

## Xavfsizlik

- Webhook faqat GitHub imzosi (HMAC-SHA256, `hmac.compare_digest`) to'g'ri kelganda
  ishlaydi; imzosiz so'rovlar `401` bilan rad etiladi.
- Faqat `push` hodisasi va faqat `BRANCH` da ko'rsatilgan branch qabul qilinadi.
- `deploy.env` faqat egasiga o'qiladigan bo'lsin: `chmod 600 deploy/deploy.env`.
- Kalitni almashtirish: `openssl rand -hex 32` → `deploy.env` va GitHub Secret'ni
  yangilang → `sudo systemctl restart ozturkapp-webhook`.
- Xizmat `root` emas, bench egasi nomidan ishlaydi; `root` faqat `supervisorctl`
  uchun sudoers orqali beriladi.
