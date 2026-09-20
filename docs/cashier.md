# Restoran kassasi (`/app/restaurant-cashier`)

Veb-kassa sahifasi: zal rejasi, buyurtmalar, hisob, to'lov, smena. Sensorli monoblokka
mo'ljallangan (baza 1366×768; 1024×768 dan 1920×1080 gacha tekshirilgan) va katta monitorda ham muvozanatli ko'rinadi.

Pulga tegadigan va siyosat talab qiladigan funksiyalar **POS Profile** darajasida
yoqiladi/o'chiriladi (xuddi «Hisobni bo'lish» kabi). Server har bir amalda bayroqni
o'zi tekshiradi — tugmani yashirish himoya emas.

## 1. Funksiyalar va bayroqlar

POS Profile → **«Restoran kassasi — qo'shimcha funksiyalar»** bo'limi. Yangi funksiyalarning
hammasi standart holatda **o'chiq** (migratsiyadan keyin ishlab turgan kassada hech narsa o'zgarmaydi);
faqat `shift_reports` yoqiq.

| Bayroq (`ctx.features`) | Maydon | Nima qiladi | Tasdiq |
|---|---|---|---|
| `split_payment` | `custom_enable_split_payment` | Bir chekni bir necha usul bilan to'lash (naqd + karta): to'lov oynasida har bir usul o'z qatori bilan chiqadi, bayroq o'chiq bo'lsa bir vaqtda faqat bittasiga summa yoziladi | — |
| `discount` | `custom_enable_cashier_discount` | Chekka chegirma (foiz yoki summa, sabab majburiy) | chegara `custom_max_cashier_discount_percent` dan oshsa menejer PIN |
| `table_transfer` | `custom_enable_table_transfer` | Stolni ko'chirish, birlashtirish, ajratish | — |
| `cashier_orders` | `custom_enable_cashier_orders` | Kassadan buyurtma: zalda / olib ketish / yetkazib berish; taom qo'shish/o'chirish | — |
| `customer_attach` | `custom_enable_customer_attach` | Mijoz qidirish, yaratish, biriktirish | — |
| `refunds` | `custom_enable_refunds` | To'langan chekni to'liq/qisman qaytarish | **har doim** menejer PIN + sabab |
| `cash_drawer` | `custom_enable_cash_drawer` | Naqd to'lovda va «G'aladon» tugmasida g'aladonni ochish | — |
| `cash_movements` | `custom_enable_cash_movements` | Smena davomida kassaga kirim/chiqim | kirim har doim; chiqim `custom_cash_payout_approval_limit` dan oshsa (0 = har doim) |
| `tips` | `custom_enable_tips` | Choychaqa (xizmat haqi va soliqqa kirmaydi) | — |
| `shift_reports` | `custom_enable_shift_reports` | X-hisobot va kassa yopilganda Z-hisobotni avtomatik chop etish | — |
| `bill_split` | `custom_enable_bill_split` | (eski) Hisobni mahsulot bo'yicha bo'lish | — |
| `virtual_keyboard` | `custom_enable_virtual_keyboard` | (eski) Ekran klaviaturasi (raqamli va matnli) | — |

Har doim yoqiq (bayroqsiz) yaxshilanishlar: kichik ekran maketi, «hisob so'radi» 🔔 belgisi,
kutish vaqti rang bilan, faol buyurtmalar tabi, bronlar ro'yxati, teginish uchun katta tugmalar.

**Ekran va maket.** Sahifa doim oddiy Desk yuqori paneli va sarlavhasi bilan ochiladi — to'liq ekran
(kiosk) rejimi foydalanuvchi talabi bilan butunlay olib tashlangan (bayroq ham, «⋯» menyudagi tugma ham yo'q).
Ilova balandligi mavjud bo'sh joydan o'lchanadi (sahifa aylanmaydi, ustunlar ichida aylanadi); shu sahifada
Desk konteyneri torayib turmasligi uchun `#page-restaurant-cashier .container { max-width: none }` qoidasi bor.
Ekran ≥1200 px bo'lganda stol tanlanmagan paytda o'ng panelda faol buyurtmalar ro'yxati (ishchi ro'yxat)
ko'rinadi; tor ekranda «Stollar | Buyurtmalar» tabi qoladi. «Barcha zallar» ko'rinishida kichik zallar yonma-yon
joylashadi. Monoblokda `chrome --app=<URL>` brauzer panelini yashiradi (Desk sarlavhasi baribir ko'rinadi).

> `custom_enable_discount` — **URY'ning o'z maydoni** (Desktop POS chegirmasi). U bilan
> bizning `custom_enable_cashier_discount` alohida. Reestr boshqa ilovaning maydoniga tegmaydi
> (`setup/cashier_features.py` va uning testi buni tekshiradi).

Yangi bayroq qo'shish: `setup/cashier_features.py::FEATURES` ga bitta yozuv — maydon avtomatik
yaratiladi, `get_cashier_context()` frontend'ga beradi, server `assert_enabled()` bilan majburlaydi.

## 2. Menejer PIN-tasdig'i

- Menejer: `User` formasida **«POS PIN»** (4–8 raqam), roli `URY Manager` yoki `System Manager`.
- Kassir tasdiq talab qiladigan amal qilsa, ekranda menejerlar ro'yxati chiqadi: menejer ismini
  bosib PIN kiritadi. Menejer o'zi kassada ishlayotgan bo'lsa PIN so'ralmaydi.
- 5 ta noto'g'ri urinishdan keyin menejer 10 daqiqaga bloklanadi; bir kassir 10 daqiqada
  ko'pi bilan 10 urinish qila oladi. Har bir tasdiq chek tarixiga (Comment) yoziladi.
- Tavsiya: 6+ xonali PIN (4 xonali PIN 10 daqiqalik oynada taxminan 0.1% xavf beradi).

## 3. Xavfsizlik qoidalari (yangi)

- **Ko'r sanoq buzilmaydi:** kassirga kutilayotgan naqd, farq va umumiy savdo smena yopilguncha
  ko'rsatilmaydi (X-hisobotda ham, chop etilgan qog'ozda ham). Hisobot topshiriqlari (`Ozturk Print Job`)
  kassirga ro'yxatda ham ko'rinmaydi.
- **Hujjat darajasidagi qo'riqchilar** (`utils/cashier_billing.py`, `utils/pos_closing.py`): oddiy
  `URY Cashier` (menejer emas) generic REST orqali qaytarish chekini yarata olmaydi, to'langan
  chekni bekor qila olmaydi, tasdiq izini o'zgartira olmaydi, chegirma funksiyasi o'chiq bo'lsa
  chegirmani oshira olmaydi, kassa yopilishini soxtalashtira yoki bekor qila olmaydi.
  Bizning API `trusted_billing()` belgisi orqali qo'riqchidan o'tadi.
- **Ofitsantga pul ko'rinmaydi:** ofitsant chek ma'lumoti oq ro'yxat (`WAITER_BILL_KEYS`) bilan
  cheklangan; stol kartasidagi summa — xizmat haqisiz taomlar summasi.
- Printerga yuboriladigan matndagi boshqaruv baytlari olib tashlanadi (g'aladon/qog'oz kesish
  buyruqlari matn ichida yashirinmasin).

## 4. Ishga tushirish (deploy) ro'yxati

`deploy/deploy.sh` quyidagilarni bajaradi: `bench build` → `bench migrate` → `clear-cache` → restart.
Qo'lda tekshirish kerak:

1. **Migratsiya** yangi DocType (`Ozturk Cash Movement`), POS Profile/User/POS Invoice maydonlari,
   «Tips Payable» va «Kassa harakati hisobi» hisoblari, `Ozturk Print Job` ruxsatlarini yaratadi.
   Hisoblar plani (buxgalter) yangi hisoblarni ko'rib chiqsin.
2. **Menejerlarga POS PIN** o'rnating.
3. **Qaytarish** ishlashi uchun POS Profile → To'lov usullari jadvalida naqd/karta usullari uchun
   «Qaytarishda ruxsat» (Allow In Returns) yoqilgan bo'lishi shart.
4. **`allow_partial_payment` = 0** bo'lishi tavsiya etiladi (1 bo'lsa URY orqali chek to'liq bo'lmagan
   to'lov bilan «To'langan» bo'lib yopilishi mumkin). Kassa ekranida bu haqda ogohlantirish chiqmaydi —
   bizning API baribir to'liq to'lov talab qiladi.
5. Menejer kassada ishlasa unga **`URY Cashier` roli ham** kerak (URY POS Invoice ruxsatini faqat
   kassirga beradi).
6. Taom o'chirish uchun URY POS Profile bayrog'i `remove_items` yoqilgan bo'lishi kerak.
7. Stol nomlari: bir nom boshqasining boshlanishi bo'lmasin («Table-1» va «Table-10» o'rniga
   «Table-01», «Table-10») — URY buyurtmani nom bo'yicha qidiradi.
8. Saytda `allow_error_traceback = 0` (kassir xato oynasida texnik izni ko'rmasin).
9. g'aladon: printer buyrug'i (`ESC p`) va g'aladon printerga ulangan bo'lishi kerak; chop etish
   agentini qayta o'rnatish shart emas (u xom baytlarni uzatadi).
10. Ofitsant ilovasini bir marta sinab ko'ring (chek ma'lumotidan pul maydonlari olib tashlandi).
11. Brauzer: monoblokda **Chrome 84+** (tavsiya: yangi versiya). `chrome --app=<URL>` rejimi brauzer
    panelini ham yashiradi.
12. Tavsiya (infra): MariaDB `READ-COMMITTED` izolyatsiyasi — qulfdan keyingi ko'p qatorli o'qishlar
    hozir `REPEATABLE READ` snapshot'iga tayanadi.

## 5. Kassa yopish arifmetikasi

Kutilayotgan naqd = boshlang'ich summa + naqd sotuv (**qaytim ayirilgan**) + kassaga kirim − chiqim −
naqd qaytarishlar. Ilgari ERPNext serverdagi hisobi qaytimni ayirmasdi (mijoz bergan 390 000, qaytim
2 000 bo'lsa 390 000 hisoblanardi) — har qaytimli smena soxta kamomad ko'rsatardi. Endi ikkala yopish
yo'li (veb-kassa va Desktop POS) `utils/pos_closing.py` dagi bitta funksiyadan foydalanadi. Eski
yopilishlarning qiymatlari o'zgarmaydi.

## 6. Ma'lum qoldiq xavflar / qarorlar

- **Desktop POS:** `getPosClosingData` (`api/desktop_pos.py`) istalgan tizimga kirgan foydalanuvchiga
  kutilgan naqd va umumiy summani beradi (ko'r sanoq). Desktop POS interfeysi shu maydonlarga tayansa,
  o'zgartirish undan oldin kelishilishi kerak. Ko'p kassirli (`Sub POS Closing`) yo'l qaytim va kassa
  harakatlarini hisobga olmaydi (bu restoranda funksiya o'chiq).
- **Chegirma yoqiq bo'lganda** limitni faqat bizning API majburlaydi; URY `make_invoice(additionalDiscount)`
  va generic `set_value` orqali chegirma limitsiz qolishi mumkin (Desktop POS'da PIN oynasi yo'q).
- Kassir POS Invoice'ga yozish huquqiga ega (URY beradi): sotuv summalarini tarix ro'yxatidan tiklab
  olishi, mahsulot narxini tahrirlashi mumkin. Uzoq muddat: yozish/bekor qilish huquqini olib, faqat API.
- Kassa tarixi oynasi har bir chekning summasi va to'lov usulini ko'rsatadi (asl xatti-harakat) —
  kassir naqd savdoni qayta hisoblashi mumkin.
- Kassa tarixi SMENA bo'yicha (kassa ochilgan vaqtdan yopilgunicha; oyna soatgacha aniq, ya'ni smena
  yarim tundan oshsa yoki bir kunda ikki smena bo'lsa ham cheklar aralashmaydi):
  - **Oddiy kassir** (`URY Cashier`) FAQAT hozirgi ochiq smenani ko'radi — tanlagich chizilmaydi, oldingi
    smenalar, ularning stol/ofitsiantlari va sana bo'yicha qidiruv unga berilmaydi. Server buni majburlaydi
    (`order._allowed_shift`): boshqa `shift` so'ralsa `CashierPermissionError`, sana e'tiborga olinmaydi,
    ochiq smena bo'lmasa ro'yxat bo'sh.
  - **Menejer** (`URY Manager`, `System Manager`) ro'yxatdan oxirgi 60 smenani tanlaydi (standart —
    hozirgi smena; yopiq smena: `POS Opening Entry.period_start_date` → `POS Closing Entry.period_end_date`).
  - Sana filtri UI'dan olib tashlangan (`get_paid_orders(date_from, date_to)` faqat menejer uchun ishlaydi).
  - Cheklov faqat kassa tarixi ro'yxati/filtrlariga tegadi: kassir POS Invoice'ni generic REST/Desk orqali
    baribir o'qiy oladi (yuqoridagi band) va `get_order_bill_preview` chek nomi bo'yicha ishlayveradi.
  - Smena oynasi cheklarni `owner` bo'yicha filtrlamaydi (yopish solishtiruvi esa smenani ochgan
    foydalanuvchining cheklarini oladi) — boshqa foydalanuvchi to'lagan chek tarixda ko'rinadi, Z-hisobotda yo'q.
- Choychaqa/chegirma tez tugmalari va `quickAmounts` so'm (UZS) uchun mo'ljallangan.
- Kassadan chiqim limiti har bir harakat uchun (smena bo'yicha yig'indi emas).
- `Ozturk Print Job` cheksiz o'sadi: davriy tozalash va `(branch, job_type, creation)` indeksi tavsiya etiladi.
- Yorug' rejimda ba'zi holat ranglarining kontrasti 4.5:1 dan past (yashil 3.3, «Band» qizil 4.1) —
  palitra dizayn qarorini kutmoqda.

## 7. Kod xaritasi

Backend (`ozturkapp/ozturkapp/`): `setup/cashier_features.py` (bayroqlar), `utils/manager_approval.py`,
`api/{billing,cashier,cashier_orders,table,order,printing,cash_movements,approval}.py`,
`utils/{discounts,refunds,shift_report,order_transfer,order_items,pos_closing,print_queue,escpos}.py`,
DocType `Ozturk Cash Movement`.

Frontend (`ozturkapp/public/js/cashier/`): ES modullar, `cashier.bundle.js`; funksiyalar
`features/<nom>/cashier_<nom>.bundle.js`. Arxitektura, slotlar va UI to'plami: `public/js/cashier/README.md`.

## 8. Testlar

```bash
cd /home/sherzod/frappe-bench
flock /tmp/ozturk_tests.lock bench --site ozturk.local run-tests --module ozturkapp.ozturkapp.tests.<modul>
# modullar: test_cashier, test_cashier_features, test_cashier_billing, test_cashier_orders,
#           test_cashier_shift, test_cashier_fe_{payment,orders,tables,shift},
#           test_cashier_review_{money,orders,frontend}, test_waiter, test_printing
```

`test_kitchen_screen_shows_three_items_out_of_four` (`test_kitchen`) bu ish boshlanishidan oldin ham
yiqilar edi va o'zgartirilmagan.

### End-to-end (haqiqiy UI + haqiqiy backend, bazaga hech narsa yozilmaydi)

`ozturkapp/ozturkapp/tests/e2e/` — «rollback shim»: bitta ochiq tranzaksiya, `commit` o'chirilgan,
oxirida har doim rollback (SIGINT/SIGTERM/SIGKILL'da ham izsiz — `verify_guarantee.py` isbotlaydi).
Brauzer (Chrome) haqiqiy backend funksiyalari bilan gaplashadi; 17 stsenariy (smena, buyurtma, to'lov usullari (naqd + karta),
choychaqa, chegirma + PIN, qaytarish, ko'chirish/birlashtirish, olib ketish/yetkazish, bron, kassa harakati,
X/Z hisobot, ko'r sanoq, bayroqlar o'chiq, bekor qilish, ofitsant, ekran o'lchamlari, poyga).

```bash
cd /home/sherzod/frappe-bench/sites
flock /tmp/ozturk_tests.lock ../env/bin/python -m ozturkapp.ozturkapp.tests.e2e.run   # ~10 daqiqa
# variantlar: --scenarios a,c   --keep-shots   --selftest   --cleanup-stale
```

Chrome kerak (`/usr/bin/google-chrome`). Batafsil: `tests/e2e/README.md`.
