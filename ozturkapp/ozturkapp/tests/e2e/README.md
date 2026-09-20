# Kassa E2E — "rollback shim"

Haqiqiy kassa interfeysi (`/app/restaurant-cashier`, Chrome) haqiqiy backend mantig'iga
qarshi yuritiladi, lekin `ozturk.local` bazasiga **hech narsa yozilmaydi**.

Bu papka `run-tests` tomonidan **yig'ilmaydi** (fayllar `test_*.py` emas, `FrappeTestCase` yo'q).

## Ishga tushirish (yagona buyruq)

```bash
cd /home/sherzod/frappe-bench/sites && \
  flock /tmp/ozturk_tests.lock ../env/bin/python -m ozturkapp.ozturkapp.tests.e2e.run
```

Qo'shimcha: `--scenarios a,c,d` (ayrim senariylar), `--keep-shots` (xato skrinshotlari
`/tmp/e2e_run/shots` da qoladi — ko'rib bo'lgach o'chiring), `--headed`, `--selftest`
(brauzersiz: faqat shim va backend), `--cleanup-stale` (SIGKILL dan qolgan `e2e-browser-tmp-*` foydalanuvchilarini o'chirish). `bench --site ozturk.local execute
ozturkapp.ozturkapp.tests.e2e.run.main` ham ishlaydi.

Talablar: `bench serve` :8000 da ishlab turishi, `/usr/bin/google-chrome`, Node 20, diskda
kamida 500 MB, portlar 8765 (shim) va 9333 (Chrome). To'liq yugurish ~12 daqiqa
(`j` senariysi ikki marta 60 soniyalik majburiy sanoqni kutadi — u serverda qattiq
konstanta, sozlama emas). `timeout` ishlatsangiz kamida 1800 s bering.

## Qanday qilib bazaga yozilmaydi

`guard.py`: bitta Python jarayoni bitta ochiq tranzaksiya (`START TRANSACTION` + SAVEPOINT)
ustida yashaydi.

| Xavf | Chora |
|---|---|
| `frappe.db.commit()` | "virtual commit": SAVEPOINT yangilanadi (kod "saqlandi" deb hisoblaydi) |
| `frappe.db.rollback()` | butun tranzaksiya emas, so'rov SAVEPOINT'iga (haqiqiy so'rov kabi) |
| `frappe.db.begin()` (`START TRANSACTION` jimgina COMMIT qiladi) | yo'q qilingan |
| DDL / `COMMIT` / `autocommit` / `LOCK TABLES` / `FLUSH`... | `frappe.db.sql`, kursor va `connection.commit` darajasida RAD ETILADI (`ForbiddenStatement`), hisobotda "HIMOYA BUZILISHLARI" |
| `frappe.enqueue` | yozib olinadi; haqiqiy ishchi (worker) rolled-back cheklarga tegmaydi. `ozturkapp.*` (va POS merge-log) ishlari shim ichida, COMMITdan keyin bajariladi; boshqalari (`frappe.*`) bajarilmaydi |
| `frappe.sendmail` | yozib olinadi, yuborilmaydi |
| chiqish (xato, `SIGINT/SIGTERM/SIGHUP`, `atexit`) | `ROLLBACK`. `SIGKILL` bo'lsa ulanish uziladi va MariaDB o'zi qaytaradi |
| Redis keshi | oxirida `frappe.clear_cache()` |

**Isbot**: ishga tushirish oldidan va keyin MUSTAQIL ikkinchi ulanish bilan BUTUN bazaning
(890 jadval) `CHECKSUM TABLE` barmoq izi + kritik jadvallar sonlari + `tabSeries` mazmuni
solishtiriladi. Farq bo'lsa jarayon xato bilan tugaydi. Boshqa (haqiqiy) sessiyalarning
`tabSessions.sessiondata` va `tabUser.last_active/last_login` kabi o'zgaruvchan ustunlari
qator darajasida e'tiborsiz qoldiriladi.

Kafolatni alohida isbotlash: `cd /home/sherzod/frappe-bench/sites && flock /tmp/ozturk_tests.lock ../env/bin/python -m ozturkapp.ozturkapp.tests.e2e.verify_guarantee`.

Yagona **commit qilinadigan** yozuv — brauzer kirishi uchun vaqtinchalik foydalanuvchi
(`e2e-browser-tmp-*@example.com`, rol `URY Cashier`). U va uning sessiyalari/izlari
(`browser_user.cleanup`) oxirida `DELETE` bilan tozalanadi va qoldiq tekshiriladi.

## Tuzilma

| Fayl | Vazifasi |
|---|---|
| `run.py` | oldindan tekshiruv, barmoq izi, foydalanuvchi, shim serveri, Node haydovchisini yurgizish, yakuniy tekshiruv |
| `guard.py` | tranzaksiya himoyasi, barmoq izi |
| `shim.py` | `/api/method/ozturkapp.ozturkapp.*` ni Frappe'ning o'z `frappe.api.handle` / `handle_exception` yo'li bilan bajaradi (xato shakli, `ApprovalRequired` 403 + `_server_messages`, `methods=[...]` cheklovi shu yerdan) |
| `fixtures.py` | tranzaksiya ichida: kassir/menejer/ofitsant, "Test Karta" (Bank, `allow_in_returns=1`) va "Test Naqd 2", `Item Price`, printerlar, `E2E-T1..3` stollari, hamma bayroq YOQIQ |
| `selftest.py` | brauzersiz tekshiruv (himoya: DDL/COMMIT rad etiladi, virtual commit haqiqiy emas) |
| `verify_guarantee.py` | SIGINT / SIGTERM / SIGKILL bilan ochiq tranzaksiyani o'ldirib, butun baza barmoq izining o'zgarmasligini isbotlaydi |
| `driver/cdp.js`, `driver/lib.js` | Chrome (CDP), `Fetch.enable` bilan `ozturkapp.ozturkapp.api.*` so'rovlarini shimga yo'naltirish, bosish/yozish, tasdiqlar |
| `driver/scenarios.js` | senariylar `a`–`q` |
| `driver/run.js` | senariylarni yuritadi, kontrakt jadvalini (non-2xx: kutilgan / NOSOZLIK) chiqaradi |

Shim foydalanuvchini cookie'dan EMAS, senariydan oladi (`actor`: kassir yoki menejer; alohida
so'rovlar uchun `apiAs(user, ...)`). Desk, aktivlar, login, realtime (socket.io, Redis orqali) —
haqiqiy serverda; shim yuborgan realtime hodisalari brauzerga HAQIQATAN yetib boradi.

## Senariylar

`a` kassa yopiq ekrani / smena ochish · `b` dine-in buyurtma (menyu, izoh, KOT, stol, hisob so'rash qo'ng'irog'i) ·
`c` hisob berish, aralash to'lov (teng bo'lish, naqd + karta), choychaqa, g'aladon, stol bo'shashi ·
`d` chegirma, `ApprovalRequired`, PIN (noto'g'ri/to'g'ri), chop etish belgisi · `e` qaytarish (`allow_in_returns`, PIN, qisman/qolgan) ·
`f` ko'chirish/birlashtirish/ajratish (+ birlashgan chekni to'lash) · `g` olib ketish/yetkazish, mijoz yaratish/biriktirish, to'lov ·
`h` bron va vaqt formati · `i` g'aladon, kassa harakati, X-hisobot (kassir: ko'r, menejer: to'liq) ·
`k` bayroqlar o'chiq: elementlar yo'qoladi, server rad etadi · `l` bekor qilish qoidalari (oshxona holati) ·
`m` ofitsant API: pul sizib chiqmasligi · `n` qaytarish buxgalteriyasi (choychaqa + chegirma) ·
`o` yorliqlar (F2/F3/F9/Esc), zal tanlash, Desk paneli ko'rinishi (to'liq ekran yo'q), joylashuvni sudrash · `p` 1024x768 / 1920x1080 sig'ishi (Desk navbari ko'rinib, sahifa aylanmaydi) ·
`q` optimistik qulf konflikti, `client_ref` va ikki marta to'lov · `j` (oxirida) kassani yopish: ko'r ikki bosqichli sanoq, Z-hisobot, GL izchilligi.

Har senariy oldidan (`a` dan tashqari) `prepare`: ochiq smena va toza zal.
Har qadamdan keyin UI matni/holati va DB haqiqati (`h.sql`, `h.op("invoice")`) tekshiriladi;
konsol xatolari, ushlanmagan istisnolar va muvaffaqiyatsiz so'rovlar qadamni yiqitadi.

## Kontrakt tekshiruvi

Brauzer yuborgan HAR BIR so'rov shim jurnaliga tushadi. Non-2xx javoblar `h.expect(method, status, excType)`
bilan e'lon qilingan kutilgan holatlarga solishtiriladi; kutilmagani, har qanday `5xx` yoki
`TypeError/KeyError/...` — `BUG?` deb belgilanadi va jarayonni yiqitadi.

## Nima tekshirilmaydi

* haqiqiy printer/agent (faqat `Ozturk Print Job` qatori);
* ≥10 chekli smenada POS konsolidatsiyasi haqiqatda ishchi navbatida bajariladi; shim buni o'z tranzaksiyasida (COMMITdan keyin) bajaradi;
* `frappe.*` fon ishlari (kontakt yaratish, gravatar) bajarilmaydi;
* bir vaqtdagi (parallel) so'rovlar — bitta ulanish tufayli ketma-ket.
