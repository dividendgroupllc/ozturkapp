# Kassa oynasi (`/app/restaurant-cashier`) — frontend arxitekturasi

Bu hujjat keyingi funksiya modullari (aralash to'lov, chegirma, stol ko'chirish,
kassadan buyurtma, g'aladon, X/Z hisobot, ...) uchun **shartnoma**. Yadro
fayllarini tahrirlamasdan yangi funksiya qo'shish uchun kerak bo'lgan hamma narsa
shu yerda.

## 1. Qat'iy qoidalar

1. **Frontend'da biznes mantiq yo'q.** Summa, soliq, ruxsat, holat — serverdan
   TAYYOR keladi. Faqat chizamiz. Tugmani `disabled` qilish qulaylik, himoya emas.
2. **Serverdan kelgan har bir matn `esc()` bilan ekranlanadi** (yoki `textContent`).
3. **Xizmat haqi foizi (12%) kodda bo'lmaydi.** Testlar tekshiradi.
4. **Foydalanuvchiga ko'rinadigan matn — o'zbekcha, `__()` orqali.**
5. **`frappe.prompt / confirm / msgprint / show_alert` ishlatilmaydi** — UI
   to'plami (`ozturk.cashier.ui`) bor. Test tekshiradi.
6. **Yangi maydon/endpoint bo'lmasa — o'chiq.** Server hali bermagan kalit
   (`undefined`) "yo'q" deb hisoblanadi; ekran sinmasligi kerak.
7. **Ko'r sanoq (blind count).** Kassirga smena yopilishidan oldin kutilayotgan
   naqd summa / farq HECH QACHON ko'rsatilmaydi. Yadro testi `Kutilgan`, `Tushum`,
   `expected_amount` so'zlarini yadro manbasidan qidiradi.
8. **Sensorli ekran:** bosiladigan element **kamida 48px** (`--rc-touch`).
9. **`hidden` atributi** Desk'da `display:none !important` — element ko'rsatilishi
   kerak bo'lsa `.hidden = false` ishlating.
10. **Brauzer: Chrome 84+** (monoblok — Windows 10, Chrome versiyasi noma'lum). Qarang §2
    «Brauzer talabi». Yangi kod bundan yangiroq API/CSS ishlatsa — zaxira yozing.
11. **Xato matni HECH QACHON `.html()` orqali matnga aylantirilmaydi** (`<img onerror>`
    ishlab ketadi): `core/api.js: errorText` inert `DOMParser` ishlatadi.
12. **Summa kiritish/o'qish `flt()` orqali EMAS** — `parseAmount` / `num`. `flt("1234.5")`
    saytning raqam formatiga (`#.###,##`) qarab `12345` beradi.

## 2. Fayllar va yig'ma (build)

```
public/js/cashier/
  cashier.bundle.js          yadro yig'masi (kirish): ES modullarni birlashtiradi
                             va `ozturk.cashier` nomlar fazosini e'lon qiladi
  core/   screen · realtime · helpers · api · prefs · build · mixin
          slots · features · shortcuts · layout
  kit/    dialog · controls · keyboard · approval · context · index   (UI to'plami)
  ui/     topbar · menu · floor · panel · orders · actions · split · modal
          payment · history · shift · shortcuts                       (ekranlar)
  util/   format.js          esc, money, groupAmount, parseAmount, elapsed*, ...
  features/                  <-- YANGI FUNKSIYA MODULLARI SHU YERDA
public/css/
  cashier.bundle.scss        uslub yig'masi (`cashier/*.css` bo'laklarini tartib bilan ulaydi)
  cashier/                   base · layout · floor · panel · modal · kit
ozturkapp/page/restaurant_cashier/
  restaurant_cashier.html    statik karkas (XOM APOSTROF YOZMANG — `&#39;`)
  restaurant_cashier.js      kirish: yig'malarni yuklaydi, Desk hayot sikli
```

### Brauzer talabi

| Narsa | Eng kam Chrome | Zaxira |
|---|---|---|
| JS sintaksisi (`?.`, `??`, `catch {}`, obyekt spread) | 58 | esbuild `es2017` ga tushiradi |
| `ResizeObserver` 64 · `Object.fromEntries` 73 · `min()/max()/clamp()` 79 | **84** gacha | — (shu tufayli pol 84) |
| flex `gap` | 84 | — |
| `100dvh` | 108 | oldin `100vh` yoziladi (barcha joyda) |
| `:has()`, `@container` | 105 | eskisida ikki ustunli to'lov/sanoq oynalari bir ustunga tushadi |
| `:where()` | 88 | eskisida qorong'i mavzuda tugma matni brauzer rangida qoladi |
| `:focus-visible` | 86 | faqat fokus ramkasi ko'rinmaydi |

**Eng kami: Chrome 84.** Haqiqatda faqat **Chrome 153** da sinalgan; qolgani statik tahlil
(`tests/test_cashier_review_frontend.py` — esbuild `chrome84` maqsadi va taqiqlangan API
ro'yxati). Yangi kod: `replaceChildren` (86), `inset` (87), `replaceAll`/`.at()` (85/92),
`structuredClone` (98), `Array.findLast` (97), `:is()` (88) kabilarni ishlatmang.

### Qanday yig'iladi

* Frappe `public/**/*.bundle.{js,css,scss}` fayllarini `bench build --app ozturkapp`
  bilan **esbuild** orqali yig'adi. Natija: `public/dist/js/cashier.bundle.<HASH>.js`
  (`dist/` git'ga tushmaydi — `public/.gitignore`) va `sites/assets/assets.json`
  ga `"cashier.bundle.js": "/assets/ozturkapp/dist/js/cashier.bundle.<HASH>.js"`.
* Sahifa `frappe.require("cashier.bundle.js")` bilan yuklaydi; Frappe nomni
  `frappe.boot.assets_json` orqali hash'li fayl yo'liga aylantiradi. **Hash kod
  o'zgarganda o'zgaradi — brauzer keshi har deployda o'zi yangilanadi** (Ctrl+Shift+R
  kerak emas). `deploy/deploy.sh` `bench build` ni allaqachon ishga tushiradi.
* Fayl nomi `assets.json` da **faqat basename** bilan turadi, shuning uchun bundle
  nomlari barcha ilovalar bo'yicha noyob bo'lishi kerak (`cashier*` prefiksi).
* Dev'da `bench watch` o'zgarishlarni kuzatadi. **Yangi `*.bundle.*` fayl
  qo'shilganda `bench watch` ni qayta ishga tushirish kerak** (fayl ro'yxati
  boshlanishida olinadi). Faqat frontend-core `bench build --app ozturkapp` ni
  ishga tushiradi.
* HTML shablon (`frappe.templates["restaurant_cashier"]`) sahifa bilan birga
  keladi, yig'maga kirmaydi; `frappe.render_template("restaurant_cashier", {})`
  bilan ishlaydi.

## 3. Nomlar fazosi `ozturk.cashier`

Funksiya modullari yadroga **ES import bilan emas**, shu global obyekt orqali
ulanadi (alohida yig'ma bo'lgani uchun yadro nusxasini o'zi bilan olib
kelmasligi va reestrlar bo'linib ketmasligi uchun).

| Nom | Nima |
|---|---|
| `ozturk.cashier.Screen` | ekran sinfi (sahifa yaratadi) |
| `ozturk.cashier.screen` | joriy ekran obyekti (nosozlik qidirish uchun) |
| `ozturk.cashier.features` | funksiya reestri (§5) |
| `ozturk.cashier.slots` | kengaytma nuqtalari (§6) |
| `ozturk.cashier.ui` | UI to'plami (§7) |
| `ozturk.cashier.api` | `call(method, args)`, `errorText(error)`, `isApprovalRequired(error)`, `ApprovalCancelled` |
| `ozturk.cashier.util` | `esc`, `money`, `num`, `fmtQty`, `groupAmount`, `parseAmount`, `bindAmountInput`, `hhmm`, `elapsedHtml`, `elapsedLevel`, `formatElapsed` |
| `ozturk.cashier.BUILD` | yig'ma hash'i (`«⋯»` menyusi tagida ko'rinadi) |

## 4. `screen` — modul ishlatishi mumkin bo'lgan a'zolar

```
screen.ctx                get_cashier_context() natijasi:
                          features, feature_settings, permissions{can_bill,is_supervisor,
                          can_operate_shift}, shift{open,...}, payment_methods, cash_modes,
                          rooms, events, warnings, branch, restaurant, cashier, ...
screen.floor / .orders    oxirgi yuklangan zal rejasi / faol buyurtmalar
screen.room               tanlangan zal (null — barcha zallar)
screen.selectedTable / .selectedInvoice
screen.detail             oxirgi chizilgan panel tafsiloti (`get_table_detail` / stolsiz buyurtma); tanlov yo'q — null
screen.view               "floor" | "orders" (keng ekranda «orders» = o'ng panel buyurtmalar ro'yxatini ko'rsatadi)
screen.wide               ilova >= 1200px keng (o'lchangan: `.rc-root[data-wide="1"]`) — o'ng panel bo'sh turganda
                          faol buyurtmalar ro'yxati, «Stollar | Buyurtmalar» almashtirgichi yo'q
screen.active             sahifa hozir KO'RINIB turibdimi (true — `resume()`dan `suspend()`gacha).
                          Fon vazifalari (davriy so'rov, ovoz) faqat `active` bo'lganda ishlasin
screen.state              "loading" | "error" | "shift" (kassa yopiq, ochish ekrani) | "ready"
screen.layoutEditMode     joylashuvni tahrirlash rejimi yoqiqmi (FAQAT O'QISH; yoqish/o'chirish —
                          `screen.toggleLayoutEdit()`). Rejimda stol bosilishi tanlash emas, sudrash
screen.floor.generated_at zal rejasi qurilgan SERVER vaqti (`YYYY-MM-DD HH:MM:SS`); brauzer soatiga
screen.floorLoadedAt      yuklangan paytdagi brauzer vaqti (ms) — birgalikda «hozir serverda soat necha»
                          (bron vaqtigacha qolgan daqiqa shunday hisoblanadi)
screen.renderFloor()      `screen.floor` dan zal rejasini qayta chizadi (o'zgartirgan bo'lsangiz —
                          masalan stolga belgi qo'shdingiz; yangi ma'lumot uchun `refresh({floor: true})`)
screen.el.canvas          zal rejasi tuvali: `.rc-table[data-table="<nom>"]` tugmalari shu yerda
                          (FAQAT O'QISH — `renderFloor()` ularni qayta yaratadi, o'z tuguningizni saqlamang)

screen.call(method, args) -> Promise<message>      (silent: xato ekranda o'zi chiqmaydi;
                                                    xato jqXHR bilan rad etiladi — `errorText(e)`)
screen.money(v)           "1 080 800"
screen.errorText(e)       o'qiladigan xato matni
screen.alertError(e)      xatoni toast qiladi (rad etilgan tasdiq — xato emas)
screen.busy(button, bool) tugmani band qiladi. `data-locked="1"` tugma `busy(false)` dan keyin ham
                          o'chiq qoladi (`PaymentSession.setConfirmEnabled` shunga tayanadi)
screen.readPreference(k) / writePreference(k, v)   qurilma sozlamasi (localStorage)

screen.refresh({floor, orders, panel, ...custom}) -> Promise
screen.refreshAll()
screen.scheduleRefresh(scope)   250ms ichida bitta so'rovga birlashtiradi
screen.listen(event, handler)   realtime obunasi (ekran o'chirilganda o'zi yechiladi)

screen.selectTable(name) / selectOrder(invoice) / clearSelection()   (Promise)
screen.openMoreSheet(detail, button)   «Yana ⋯» varag'ini ochadi (dasturiy)
screen.triggerAction(id)        paneldagi faol tugmani bosadi (`data-action=id`)
screen.setView("floor"|"orders")
screen.openPaymentModal(detail) / openHistoryModal() / closeShiftDialog()
screen.showModal(title, {size: "md"|"lg"|"xl", locked}) / closeModal()
screen.el.*                     DOM havolalari (rooms, panel, orderList, overlay, modalBody, ...)
```

`screen.ctx.features[flag]` — POS Profile bayroqlari. Server ham har amalda
`cashier_features.assert_enabled()` bilan tekshiradi.

## 5. Funksiya reestri

```js
const { features, slots, ui } = ozturk.cashier;

features.register({
	key: "discount",          // noyob nom
	flag: "discount",         // ctx.features[flag] rost bo'lsagina o'rnatiladi; yo'q — doim
	install(screen) {
		const off = [];
		off.push(slots.contribute("panel.secondary", { ... }));
		off.push(slots.contribute("payment.afterAmount", { ... }));
		screen.listen("event_nomi", (data) => screen.scheduleRefresh({ panel: true }));
		return off;           // olib tashlovchi funksiya(lar) — ixtiyoriy
	},
});
```

* `install(screen)` `boot()` da, **birinchi chizishdan oldin** chaqiriladi. Smena
  ochilgach kontekst qayta o'qilganda `features.sync()` bayroq o'zgarganini ko'rsa,
  o'chganini olib tashlaydi (qaytarilgan funksiyalar chaqiriladi).
* `screen.listen()` obunalari `screen.destroy()` da yechiladi. `boot()` qayta ishlaganda
  (smena ochilgach) faqat YADRO obunalari yangilanadi — `install()` da olingan obunalaringiz qoladi.

### Yangi funksiya qo'shish (qadamlar)

1. `public/js/cashier/features/<nom>/cashier_<nom>.bundle.js` yarating (kirish;
   yonida istalgan yordamchi fayllar — ular ES import bilan shu yig'maga kiradi).
   Fayl nomi **`cashier_<kichik_harf_raqam_pastki_chiziq>.bundle.js`** bo'lishi SHART —
   sahifa aynan shu andoza bo'yicha topadi (`assets.json` dan).
2. Uslub kerak bo'lsa `cashier_<nom>.bundle.css` (yoki `.scss`) — xuddi shunday
   topiladi va yadrodan KEYIN yuklanadi. Selektorlar `.rc-<nom>-…` bilan boshlanadi;
   ranglar `--rc-*` o'zgaruvchilardan.
3. Modul ichida faqat `ozturk.cashier` dan foydalaning; `features.register(...)`.
4. Serverga so'rovlar `screen.call(...)`; yangi endpoint `scope`/`assert_enabled`
   tekshiruvlarini o'zi qiladi.
5. Testni O'Z fayllaringizga yozing (yadro `tests/test_cashier.py` ga tegmang).
6. Yig'ish uchun frontend-core / main agentdan `bench build --app ozturkapp`
   so'rang (dev'da yangi fayl bo'lsa `bench watch` qayta ishga tushadi).

**Yadro fayllarini tahrirlamang:** `restaurant_cashier.js`, `cashier.bundle.js`,
`hooks.py` ga hech narsa qo'shish shart emas.

## 6. Kengaytma nuqtalari (slot)

`slots.contribute(name, item) -> dispose()`. Har elementda `id` (slot ichida noyob)
va `order` (kichigi oldin, standart 100) bo'ladi. Asosiy tugmalar ham shu
yo'l bilan qo'shilgan — o'z `order` ingizni ular orasiga qo'yishingiz mumkin.
Noma'lum slot nomi — xato. `when(...)` berilmasa — doim ko'rinadi.

| Slot | Element | Argumentlar |
|---|---|---|
| `topbar.menu` | `«⋯»` menyu bandi (tartib: yadro 10–40, funksiya modullari 100–899; qizil «Kassani yopish» `order: 900` — eng pastda) | `when(screen)`, `checked(screen)`, `disabled(screen)`, `onClick(screen)` |
| `topbar.quick` | yuqori paneldagi tez tugma (ikonka + yozuv), «⋯» ning yonida | `when(screen)`, `label`, `icon`, `kind`, `disabled(screen)`, `onClick(screen, button)` |
| `panel.info` | panel sarlavhasi va taomlar ro'yxati orasida qotirilgan 1-2 qatorli blok | `when(screen, detail)`, `render(screen, detail)` |
| `panel.primary` | panel pastidagi KATTA tugma (ko'pi bilan 2 ustun) | `when(screen, detail)`, `label`, `disabled(screen, detail)`, `onClick(screen, detail, button)` |
| `panel.secondary` | panel pastidagi qator tugmasi (ko'pi bilan 4 ta; yadroda 3 ta — ortig'i `panel.more` ga) | `panel.primary` bilan bir xil |
| `panel.more` | «Yana ⋯» varag'idagi amal (ortiqcha amallar: chegirma, ko'chirish, ...) | `panel.secondary` bilan bir xil |
| `payment.beforeAmount` | to'lov oynasi: usullardan keyin, summa maydonidan OLDIN | `when(session)`, `render(session)` |
| `payment.afterAmount` | summa/tez summalardan KEYIN, qaytimdan oldin | `when(session)`, `render(session)` |
| `payment.footer` | «To'lovni tasdiqlash» tugmasi tepasida | `when(session)`, `render(session)` |
| `orders.tabs` | buyurtmalar ro'yxati filtri (tor ekranda chap ko'rinishda, keng ekranda o'ng paneldagi ro'yxatda — bir xil) | `when(orders)`, `label`, `filter(order)`, `badge(orders)` |
| `floor.tileBadges` | stol kartasidagi kichik belgi | `when(table, screen)`, `badge(table, screen)` |
| `history.rowActions` | tarix qatoridagi tugma | `when(screen, row)`, `label`, `kind`, `onClick(screen, row, button)` |
| `history.detailActions` | tarix tafsilotidagi tugma | `when(screen, bill)`, `label`, `kind`, `onClick(screen, bill, button)` |
| `shortcuts` | klaviatura yorlig'i | `key`, `description`, `when(screen)`, `handler(screen, event)`, `allowInInput` |
| `realtime` | realtime hodisa ishlovchisi | `event` (nom yoki `(screen) => nom`), `handler(data, screen)` |
| `refresh` | har `screen.refresh()` dan keyin | `run(screen, scope)` (xatosi ekranni to'xtatmaydi) |

Element shakllari:

```js
// topbar.menu — «⋯» menyusi (48px+ qatorlar)
{ id, order, label, kind: "danger"?, when(screen), checked(screen) /*true|false — «Yoqiq/O'chiq»*/,
  disabled(screen) /*false | true | "sabab matni"*/, onClick(screen) }

// topbar.quick — 52px yuqori panelda «⋯» ning chap tomonida; >=48px nishon.
// <=1100px kenglikda yozuv yashiriladi (faqat ikonka; aria-label/title qoladi).
{ id, order, label, icon?: "🧾" /* emoji yoki qisqa belgi; yo'q bo'lsa yozuvning bosh harfi */,
  kind: "primary"|"default", when(screen),
  disabled(screen) /*false | true | "sabab matni" — sabab bosilganda toast bo'lib chiqadi*/,
  onClick(screen, button) }
// Holat o'zgarganda (yuklash, tanlov) qayta chiziladi; ko'p qo'shmang: 1024px da
// ikonka-tugmalar 3-4 tagacha sig'adi.

// panel.info — sarlavha bilan ro'yxat orasida QOTIRILGAN blok (yetkazib berish telefoni/manzili,
// biriktirilgan mijoz, chegirma xulosasi, «chek o'zgargan — qayta chop eting» ogohlantirishi).
// Har blok 1-2 qator. Bo'sh bo'lsa balandligi 0. Har `renderPanel` da qayta chiziladi.
{ id, order, when(screen, detail), render(screen, detail) -> HTMLElement | "<html>" }
// Taomlar ro'yxatiga kamida ~120px qolishi kafolatlanadi: sig'masa ortiqcha bloklar
// bitta «+N yana» qatoriga yig'iladi (bosilsa ochiladi). `render` xatosi panelni buzmaydi.
// Serverdan kelgan matnni `ozturk.cashier.util.esc()` bilan ekranlang.

// panel.primary / panel.secondary / panel.more — detail.kind: "bill" | "available" | "reserved" | "issue"
{ id, order, kind: "primary"|"pay"|"danger"|"danger-solid"|"default",
  label: "matn" | (screen, detail) => "matn",
  when(screen, detail), disabled(screen, detail) /*false | true | "sabab"*/,
  onClick(screen, detail, button) }
// `data-action="<id>"` bo'ladi; F2/F3 yorliqlari `screen.triggerAction("pay"|"give-bill")` ishlatadi.
// Asosiy id lar: reserve, unreserve, reload, give-bill, pay, split-bill, reprint,
// cancel-order, release.
// `panel.more` — «Yana ⋯» tugmasi (48px) panel pastidagi qatorning OXIRIDA; kamida bitta
// element ko'ringandagina chiqadi va katta qatorli varaqni ochadi (ikonkasiz, `kind` rangi,
// `disabled` sababi qator ostida matn bo'lib ko'rinadi). Element `panel.secondary` bilan
// bir xil shaklda; tugma bosilganda varaq yopilib, `onClick(screen, detail, button)`
// chaqiriladi (`button` — «Yana ⋯» tugmasi).

// payment.* — bo'lim
{ id, order, when(session), render(session) -> HTMLElement | "<html>" }

// orders.tabs
{ id, order, label, filter(order) -> bool, badge(orders) -> son }

// floor.tileBadges
{ id, order, when(table, screen), badge(table, screen) -> "matn" | {text} | null }

// history.rowActions / history.detailActions
// (qaytarish cheklarining qizil «QAYTARISH» belgisi va «Asl chek» havolasi yadroda —
//  `get_paid_orders` va `get_order_bill_preview` dagi `is_return` / `return_against` bo'yicha;
//  buning uchun slot kerak emas)
{ id, order, label, kind, when(screen, subject), onClick(screen, subject, button) }

// shortcuts — matn yozilayotganda (fokus maydonda) ISHLAMAYDI; `allowInInput: true` bundan mustasno
{ id, key: "F2" | "Escape" | "Ctrl+K" | "Shift+F3", description, when(screen), handler(screen, event) }

// realtime
{ id, event: "nom" | (screen) => "nom", handler(data, screen) }

// refresh — `scope`: {floor, orders, panel, ...} (scheduleRefresh'ga uzatilgan har qanday kalit)
{ id, run(screen, scope) }
```

### `PaymentSession` (to'lov oynasi holati)

`payment.*` slot `render(session)` shu obyektni oladi (DOM bilan emas, shu bilan ishlang):

```
session.screen / .detail / .bill / .methods
session.$body                     oyna tanasi (jQuery); `.rc-pay__input` (har bir usul uchun bittadan, `data-mode` = usul nomi), `.rc-pay__due-value`, ...
session.due                       to'lanadigan summa (server `bill.payable`, bo'lmasa `rounded_total`)
session.multi                     bir nechta usulga summa yozish mumkinmi (POS Profile `split_payment` bayrog'i)
session.amount                    kiritilgan JAMI summa (barcha usullar yig'indisi, son)
session.entries() / payments()    summa kiritilgan usullar: `[{mode_of_payment, amount}]` (`submit_payment` shuni oladi)
session.onChange(fn)              summa / to'lanadigan summa o'zgarganda
session.changed()                 holat qatorini (yetishmaydi / to'liq / qaytim) qayta hisoblaydi va tinglovchilarni chaqiradi
session.setDue(number)            to'lanadigan summani o'zgartiradi (masalan choychaqa qo'shildi); to'liq summa turgan usul unga ergashadi
session.setArg(name, value)       `submit_payment` ga qo'shimcha parametr (`tip`); undefined — olib tashlash
session.setError(text)            xato matni «To'lovni tasdiqlash» tepasida (qotirilgan qator — ustun aylansa ham ko'rinadi)
session.numpad                    ekran raqam paneli (klaviatura o'chiq bo'lsa — null)
session.bindNumpad(input)         yangi kiritish maydonini SHU panelga ulaydi (dinamik qatorlar, tahrirlagich) — ikkinchi
                                  panel qo'ymang. Panel yo'q bo'lsa hech narsa qilmaydi (false qaytaradi). Panel faqat
                                  KO'RINIB turgan maydonga yozadi (yashirilgan rejim maydonlari tashlab ketiladi)
session.setConfirmEnabled(bool)   «To'lovni tasdiqlash» ni yoqadi/o'chiradi. `screen.busy(false)` dan OMON QOLADI —
                                  MutationObserver qo'riqchisi kerak emas
```

`payment.*` bo'limining `render(session)` xatosi butun oynani buzmaydi: xatoli bo'lim tushib qoladi va
`console.error` ga yoziladi. `submit_payment` menejer tasdig'ini talab QILMAYDI (chegirma va qaytarish o'z
oqimida `ui.withApproval` bilan so'raydi). Oyna tuzilishi: chap ustun (`.rc-pay__main`) ichkarida aylanadi,
raqam paneli va pastki qator (xato, `payment.footer`, «To'lovni tasdiqlash») joyida turadi.

## 7. UI to'plami — `ozturk.cashier.ui`

Hammasi sensorli (48px+ nishonlar), ekran klaviaturasiga tayyor, `.rc-root` ichida
chiziladi (Desk uslubi tushmaydi). Kichik oynalar STEK: to'lov oynasi ustida PIN
oynasi ochilishi mumkin; `Esc` eng ustkisini yopadi.

```js
ui.dialog({ title, subtitle?, size?: "sm"|"md"|"lg"|"xl", note?, body?, fields?, actions?, dismissible? })
    -> Dialog { overlay, dialogEl, bodyEl, footEl, result: Promise, values(), validate(), setError(text),
                setBusy(bool), focus(), close(value) }
    // actions: [{ id, label, kind: "primary"|"pay"|"danger"|"danger-solid"|"default", onClick(dialog), disabled }]
    // body: HTML satr (ekranlangan!) | Node | (dialog) => Node|satr

ui.form({ title, subtitle?, note?, fields, submitLabel?, cancelLabel?, kind?, size?, onSubmit?(values, dialog) })
    -> Promise<values | null>
    // onSubmit xato tashlasa oyna YOPILMAYDI va xato oynada chiqadi; muvaffaqiyatda yopiladi.
    // `required` maydonlar avtomatik tekshiriladi.

ui.confirm({ title, message, confirmLabel?, cancelLabel?, kind?, size? })  -> Promise<boolean>
ui.alert({ title, message, okLabel?, big?, size? })                        -> Promise<void>
ui.toast(message, { indicator?: "green"|"red"|"orange"|"blue", seconds?: 5, title? })
    // oyna/modal ochiq bo'lsa bildirishnoma TEPADA chiqadi va bosishni ushlamaydi (pointer-events: none) —
    // pastki tugmalarni yopib qo'ymaydi
ui.closeTop() -> boolean     ui.hasDialog() -> boolean
    // Tab oyna ichida aylanadi (orqadagi tugmalarga o'tmaydi); yopilganda fokus oynani ochgan
    // elementga qaytadi. `dialog.setBusy(true)` (so'rov ketyapti) paytida ×, Esc va orqa fon
    // oynani YOPMAYDI — natija/xato ko'rinmay qolmasin. Dasturiy `close()` har doim ishlaydi.

ui.chips({ options, value?, multiple?, columns?, onChange?, other?: { label, placeholder } })
    -> { el, value(), set(v), input }
    // options: ["matn", ...] | [{ value, label }]; `other` — «Boshqa…» tugmasi: tanlansa
    // matn maydoni ochiladi va value() o'sha matn bo'ladi.

ui.amountInput({ value?, onChange?(number), label? }) -> { el, value(), set(n), focus() }
    // "1 080 800" guruhlanadi, kursor joyida qoladi (to'lov/sanoq maydonlari bilan bir xil mantiq)

ui.requestApproval(actionLabel, { error? }) -> Promise<{ user, pin } | null>
ui.withApproval(fn, { action? })            -> Promise<natija>     // fn: async (approval|null) => natija
ui.ApprovalCancelled                          // withApproval foydalanuvchi bekor qilganda tashlaydi

ui.keyboard.numpad({ inputs? }) -> HTMLElement      // oyna ichiga qo'yiladigan raqam paneli; `el.bind(input)` keyin maydon ulaydi
ui.keyboard.TextKeyboard(host, { onEnter })        // matn klaviaturasi (o'zbek lotin / kirill / belgi / raqam)
ui.keyboard.insertText(input, text) / backspace(input)
ui.virtualKeyboard                                 // ctx.features.virtual_keyboard
```

**Forma maydonlari** (`fields: [...]`), umumiy: `{ type, name, label, required, hint, value, validate(value) -> xato matni }`

| `type` | Qiymat | Qo'shimcha |
|---|---|---|
| `text` | matn | `placeholder` |
| `textarea` | matn | `rows` |
| `tel` | matn | raqam-klaviatura (`+ 0-9`) |
| `amount` | son | probel bilan guruhlanadi, raqam-klaviatura |
| `select` | tanlangan qiymat | `options`, `columns`, `other` |
| `chips` | massiv (`multiple: true`) yoki qiymat | `options`, `columns` |
| `time` | `"HH:MM"` | soat (24) + daqiqa (00/15/30/45) tugmalari |
| `note` | — | `text` (faqat ko'rsatish) |

### Ekran klaviaturasi

`ctx.features.virtual_keyboard` yoqiq bo'lsa: dialog ichidagi matn maydoniga fokus
tushganda klaviatura oynaning pastida ochiladi (o'zbek lotin `oʻ gʻ sh ch ʼ`,
kirill `ў қ ғ ҳ`, raqam/belgi, bosh harf, bo'sh joy, o'chirish, `↵`), raqam
maydonlarida — katta raqam paneli. Tugmalar fokusni olmaydi. O'chiq bo'lsa —
faqat brauzerning o'z klaviaturasi. Maydonlar `data-vk="text|numeric|tel"` bilan
belgilanadi (`ui.form` o'zi qo'yadi); o'z maydoningizga shu atributni bering.

### Menejer tasdig'i

```js
try {
	await ui.withApproval(
		(approval) => screen.call("ozturkapp.ozturkapp.api.billing.apply_discount", {
			invoice, percent, reason, ...(approval ? { approval: JSON.stringify(approval) } : {}),
		}),
		{ action: __("Chegirma {0}%", [percent]) }
	);
} catch (error) { screen.alertError(error); }   // ApprovalCancelled xato sifatida ko'rinmaydi
```

* `fn(null)` avval tasdiqsiz chaqiriladi. Server `exc_type == "ApprovalRequired"`
  (`error.responseJSON.exc_type`) qaytarsa — menejer ismi tanlanadi, PIN kiritiladi
  va `fn({user, pin})` qayta chaqiriladi; PIN noto'g'ri bo'lsa server yana
  `ApprovalRequired` beradi — oyna xato matni bilan qayta ochiladi.
* Menejerlar ro'yxati (`approval.get_approvers`) 1 daqiqa keshlanadi. Foydalanuvchining
  o'zi menejer (`self_approves`) bo'lsa HECH NARSA so'ralmaydi.
* **Server talabi:** `ApprovalRequired` `frappe.throw(msg, exc=ApprovalRequired)`
  bilan tashlanishi kerak. Oddiy `raise ApprovalRequired(msg)` da xabar JSON'ga
  tushmaydi va Frappe 403 da o'zining «Not permitted» oynasini (`silent` bo'lsa
  ham) ochib yuboradi.

## 8. Ko'rinish: CSS o'zgaruvchilari va DOM kelishuvlari

### Ekran tuzilishi

```
Desk navbar + sahifa sarlavhasi                 (HAR DOIM ko'rinadi)
┌ .rc-root ─────────────────────────────────────────────────────────┐
│ .rc-topbar 52px: zallar | holat filtri | smena, kassir, tez tugmalar, soat, «⋯» │
│ ┌ .rc-left ───────────────────────┐ ┌ .rc-panel  clamp(340px, 36%, 600px) ┐ │
│ │ .rc-viewtabs (faqat tor ekranda)│ │ hisob  YOKI  (keng ekran) buyurtmalar │ │
│ │ zal rejasi | buyurtmalar (tor)  │ │ ro'yxati  YOKI  (tor ekran) yo'l-yo'riq│ │
│ └─────────────────────────────────┘ └────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

* **O'lchamlar JS bilan o'lchanadi** (`core/layout.js`), oyna kengligiga emas ilovaning O'Z
  o'lchamiga qarab, `.rc-root` da atribut bo'lib turadi (CSS shularga tayanadi):
  `data-wide="1|0"` (>= 1200px), `data-short="1|0"` (ilova balandligi < 640px),
  `data-compact="0..5"` (yuqori panel sig'masa qismlar birma-bir yig'iladi: 1 kassir nomi ·
  2 filtr yozuvi (tanlangandan tashqari) · 3 tez tugma yozuvi · 4 zallar soni va «Kassa ochiq»
  yozuvi · 5 zallar aylantiriladi). Balandlik `--rc-offset` (Desk ustidan/ostidan olgan joyi).
* **Keng ekran (`data-wide="1"`)**: hech narsa tanlanmaganda o'ng panel = FAOL BUYURTMALAR ro'yxati
  (`orders.tabs` filtrlari, «hisob so'radi» birinchi, keyin eng uzoq kutgani — tor ekrandagi
  «Buyurtmalar» ko'rinishi bilan bir xil qatorlar). Stol yoki qator tanlansa panel hisobga
  almashadi; sarlavhadagi `‹` (`data-action="panel-back"`) ro'yxatga qaytaradi. Ro'yxatda pul
  jami yo'q — faqat soni (ko'r sanoq). **Tor ekran**: yuqori panel ostida «Stollar | Buyurtmalar»
  almashtirgichi (`.rc-viewtabs`), bo'sh panelda qisqa yo'l-yo'riq. `screen.setView("orders")`
  keng ekranda ro'yxatga qaytaradi (tanlov bo'lsa bekor qiladi), `setView("floor")` tanlovga tegmaydi.
* **Zal rejasi** (`ui/floor.js`): masshtab 0.7..1.15 (stol tabiiy o'lchamiga yaqin; ilgari 1.5 —
  «semiz» edi), chapga-yuqoriga tekislangan, nuqtali to'r fonida. «Barcha zallar»da har zal
  chap-yuqoriga tekislangan blok; bloklar `packBlocks` bilan mavjud joyga qarab yonma-yon
  qatorlarga joylanadi (eng katta masshtab yutadi; eniga sig'maydigan bo'linish faqat boshqa yo'l
  bo'lmasa), qatordagi bloklar bo'sh joyni teng bo'lishadi — zal sarlavhasi va uning chizig'i
  blokning to'liq eniga yetadi. Server javobi (`floor.tables[].layout`, `room_bands`, `extent`)
  o'zgartirilmaydi: qo'shimcha joylashuv `arrangeFloor()` da alohida hisoblanadi; bitta zalda
  (joylashuvni tahrirlash mumkin bo'lgan yagona ko'rinish) stollar saqlangan koordinatada turadi.
* **Sahifa kengligi**: `#page-restaurant-cashier .container { max-width: none }` (`layout.css`) —
  Desk navbar/sarlavhasi joyida, faqat shu sahifaning ish maydoni to'liq enga ochiladi.

### O'zgaruvchilar

`.rc-root` da (Desk mavzusidan olinadi — dark rejim o'zi ishlaydi):

```
--rc-touch: 48px            bosiladigan element eng kami
--rc-gap, --rc-radius (10px), --rc-radius-lg (14px)
--rc-bg, --rc-surface, --rc-sunken, --rc-border, --rc-text, --rc-muted
--rc-line                   boshqaruv elementi chegarasi (fon bilan >= 3:1; --rc-border esa bezak chizig'i)
--rc-available / --rc-reserved / --rc-occupied              holat rangi: chegara, nuqta, to'ldirish (>= 3:1)
--rc-available-ink / -reserved-ink / -occupied-ink          shu rangdagi MATN (>= 4.5:1, tus fonida ham)
--rc-available-soft / -reserved-soft / -occupied-soft        och tusli fon
--rc-available-fill         to'ldirilgan tugma («To'lov»): ustidagi oq matn >= 4.5:1 (ikkala mavzuda)
--rc-accent (to'ldirish), --rc-accent-ink (matn), --rc-accent-soft (tanlangan holat foni)
--rc-shadow-1/2/3           yengil / ko'tarilgan / oyna soyasi
```

Palitra: yorug' mavzuda matn 5.0:1 dan yuqori (yashil `#166534` 7.1, qizil `#991b1b` 8.3, sariq `#92400e` 7.1,
ko'k `#1d4ed8` 6.7), qorong'i mavzuda `-ink` ranglar ochroq (`#4ade80`, `#f87171`, `#fbbf24`, `#93c5fd`).
`tests/test_cashier_design.py` bu nisbatlarni CSS'dan hisoblab tekshiradi. Rangli MATN uchun har doim `-ink`,
chegara/nuqta uchun asosiy rang. **Tanlangan holat** (zal, filtr, tab) — ko'k tusli fon + ko'k matn + ko'k chegara
(qora to'ldirilgan «tabletka» yo'q). **Fokus halqasi faqat klaviaturada** (`:focus-visible`); sichqoncha/barmoq
bosishidan keyin tugmada halqa qolmaydi.

* Tugma: `.rc-btn` + `.rc-btn--primary | --pay | --danger | --danger-solid`.
  Maydon: `.rc-input`. Tanlov: `.rc-chip-opt[aria-pressed]`. Holat belgisi:
  `.rc-chip--AVAILABLE|RESERVED|OCCUPIED`, `.rc-tag`.
* Holat FAQAT rang bilan emas — belgi + matn bilan ham.
* Vaqt: serverning `Time` maydoni `9:00:00` (nol qo'yilmagan soat) bo'lib keladi — `slice(0, 5)` `9:00:` beradi.
  Hamma joyda `ozturk.cashier.util.hhmm(value)` (`9:00:00`, `09:00`, `2026-09-20 9:05:00`, null → `""`) ishlating.
* O'tgan vaqt: `ozturk.cashier.util.elapsedHtml(minutes, loadedAt, short?)` — brauzer
  o'zi har 10 soniyada oshiradi (>60 daq sarg'ayadi, >90 daq qizaradi).
* «Hisob so'radi» belgisi: `<span class="rc-bell">`; e'tibor pulsatsiyasi
  `prefers-reduced-motion` da o'chadi (qalin chegara qoladi).
* **To'liq ekran (kiosk) rejimi ATAYLAB olib tashlangan** va qaytarilmaydi: kassa har doim
  oddiy Desk sahifasi — navbar va sahifa sarlavhasi ko'rinib turadi, `body` sinfi qo'yilmaydi,
  `localStorage`da kiosk kaliti yo'q, POS Profile'da bayrog'i yo'q. Ilova balandligi ekran
  o'lchamidan emas, `.rc-root` ning HAQIQIY o'rnidan hisoblanadi (`core/layout.js: syncOffset` —
  Desk paneli qancha joy olsa, qolganini ilova oladi; sahifa aylanmaydi, ustunlar ichkarida aylanadi).
* Brauzer: Chrome 84+ (§2 «Brauzer talabi»); `100dvh`ning `100vh` zaxirasi bor, `:has()` va
  konteyner so'rovlari 105+ — eskisida ikki ustunli to'lov/sanoq oynalari bir ustunga tushadi.
* Stol kartasi: bir xil tuzilish (hamma holat va shakl uchun) — yuqori chetda o'tgan vaqt tabletkasi
  (`.rc-table__age`), o'rtada nom · holat (belgi + matn) · summa / o'rinlar / bron vaqti · ofitsant,
  pastki chetda belgilar tabletkasi (`.rc-table__flags`: 🔔, 🧾, ⛓, ×N, `floor.tileBadges`).
  O'rinlar soni yo'q stolda qator bo'sh qoladi (tekislik saqlanadi). 1 dan kichik masshtabda matn
  `1/masshtab` ga kattalashtiriladi (ekranda nomi >= 16px, qolgani >= 12px), 0.85 dan kichikda
  ofitsant qatori tushadi. Kartaga uzun matn qo'shmang; belgi uchun `floor.tileBadges`
  (qisqa: emoji yoki 2-3 belgi).

## 9. Serverdan kutiladigan (ixtiyoriy) kalitlar

Quyidagilar bo'lmasa tegishli belgi shunchaki chizilmaydi:

* zal rejasi stoli va `get_active_orders` qatori: `bill_requested`, `elapsed_minutes`
  (`opened_at`, `order_type`, `delivery` — modullar uchun);
* `build_bill()`: `payable` (to'lanadigan summa), `tip`, `discount_percent`,
  `bill_requested`, `is_return`, ...;
* `ctx.features.<flag>`; `ctx.feature_settings` (`max_cashier_discount_percent`,
  `cash_payout_approval_limit`, `tip_percent_options`).

## 10. Tekshirish

```bash
cd /home/sherzod/frappe-bench
bench build --app ozturkapp                          # yig'ish (faqat frontend-core)
flock /tmp/ozturk_tests.lock bench --site ozturk.local run-tests \
      --module ozturkapp.ozturkapp.tests.test_cashier
```

`tests/test_cashier.py` yadro manbasini (`features/` dan tashqari) matn bo'yicha
tekshiradi: Desk oynalari yo'qligi, foiz yo'qligi, slotlar hujjatlangani, yig'ma
`assets.json` da borligi, HTML shablonda xom apostrof yo'qligi.
