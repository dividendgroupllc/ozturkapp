"""URY dinamik narxlash — talabga qarab narxni avtomatik boshqarish.

Mahsulot narxi treyding bozoridagidek harakatlanadi: ko'p sotilgani qimmatlashadi,
sotilmagani arzonlashadi. Narx hech qachon tannarxdan past tushmaydi va bazaviy
narxdan belgilangan koridordan (standart ±10%) chiqmaydi.

Modul ikki qismdan iborat:

1. **Sof hisoblash** — `compute_next()` va uning yordamchilari. Bular `frappe` ga
   umuman tegmaydi, shuning uchun testda 465 tovar × 240 sikl bo'yicha bir necha
   soniyada yugurtirish mumkin (`ury/tests/test_dynamic_pricing.py`).
2. **Frappe qatlami** — sozlama, talab so'rovi, tannarx zanjiri, yozuv va
   `run_due_branches()` scheduler kirish nuqtasi.

Narx ikki joyga yoziladi va bu **majburiy**:

* `URY Menu Item.rate` — POS `getRestaurantMenu` orqali shuni ko'radi;
* `Item Price.price_list_rate` — `ury_order.sync_order` invoys qatorini
  aynan shundan narxlaydi (`ury_order.py` ichida POS yuborgan `rate` e'tiborga
  olinmaydi).

Faqat bittasini yozish "ko'rsatilgan narx ≠ hisoblangan narx" holatiga olib keladi.

DIQQAT: `URY Menu` hujjatini `save()` qilmang — `URY Menu.on_update` butun
`Item Price` ro'yxatini o'chirib qaytadan yaratadi (`ury_menu.py:make_price_list`).
Faqat `frappe.db.set_value` ishlating.
"""

import json
import math
import time

# ═══════════════════════════════════════════════════════════════════
#  Sozlamalar
# ═══════════════════════════════════════════════════════════════════

#: Filial sozlamasi bo'sh bo'lganda ishlatiladigan qiymatlar.
#: Filialga xos qiymatlar `Branch.custom_dynamic_pricing` (JSON) da saqlanadi.
DEFAULTS = {
    # ── Asosiy o'chirgichlar ──────────────────────────────────────
    "enabled": 0,
    "dry_run": 1,
    "interval_minutes": 60,
    "apply_window": "",          # "04:00-11:00" — bo'sh bo'lsa har doim
    # ── Talab oynalari (7 ga karrali bo'lishi shart) ──────────────
    "recent_days": 7,
    "baseline_days": 28,
    # ── Chegaralar ────────────────────────────────────────────────
    "max_up_pct": 10.0,
    "max_down_pct": 10.0,
    "max_step_pct_per_cycle": 2.0,
    "max_step_pct_per_day": 4.0,
    "min_change_interval_hours": 6,
    # ── Yaxlitlash ────────────────────────────────────────────────
    "rounding_step": 500,
    "rounding_ladder": [1000, 500, 100, 50],
    "hysteresis_factor": 0.6,
    "min_price_for_dynamic": 2000,
    # ── Sezgirlik ─────────────────────────────────────────────────
    "gain": 0.02,
    "deadband": 0.15,
    "confidence_units": 10,
    "prior_alpha": 1.0,
    "prior_min": 0.5,
    "reversion": 0.5,
    "new_item_grace_days": 14,
    # ── Tannarx ───────────────────────────────────────────────────
    "apply_cost_floor": 1,
    "min_margin_pct": 0.0,
    "cost_source_order": ["bom", "bin", "valuation_rate", "last_purchase_rate", "cogs"],
    # ── Istisnolar ────────────────────────────────────────────────
    "excluded_item_groups": [],
    "excluded_courses": [],
    "excluded_items": [],
    # ── Xizmat ────────────────────────────────────────────────────
    "demand_source": "pos_invoice",   # yoki "synthetic" (faqat dev)
    "dry_run_persists_shadow": 1,
    "price_mismatch_policy": "warn",  # warn | block
    "retention_days": 400,
}

DAY = 86400.0

#: `compute_next` qaytaradigan holatlar.
STATUS_AUTO = "auto"
STATUS_LOCKED = "locked"
STATUS_NEW = "new"
STATUS_NO_DATA = "no_data"
STATUS_NO_BASE = "no_base"
STATUS_COST_VIOLATION = "cost_violation"
STATUS_EXCLUDED_LOW_PRICE = "excluded_low_price"
STATUS_EXCLUDED = "excluded"


def merge_settings(raw) -> dict:
    """Saqlangan JSON sozlamani `DEFAULTS` ustiga qo'yish.

    Noto'g'ri yoki buzilgan JSON — jimgina standart qiymatlarga qaytadi;
    narxlash tizimi sozlama xatosi tufayli to'xtab qolmasligi kerak.
    """
    cfg = dict(DEFAULTS)
    if not raw:
        return cfg
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return cfg
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in DEFAULTS:
                cfg[key] = value
    return cfg


# ═══════════════════════════════════════════════════════════════════
#  Sof hisoblash — bu yerdan pastda `frappe` ishlatilmaydi
# ═══════════════════════════════════════════════════════════════════

def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def choose_grid(lo_band: float, hi_band: float, cfg: dict) -> int:
    """Koridorga kamida 3 ta nuqta sig'adigan eng katta yaxlitlash qadami.

    500 UZS panjara 10 000 UZS tovar uchun to'g'ri, lekin 1 500 UZS tovarning
    ±10% koridori (1 350…1 650) ichiga bitta ham 500 lik nuqta tushmaydi —
    narx abadiy muzlab qolardi. Shuning uchun qadam zarur bo'lsa
    1000 → 500 → 100 → 50 tartibida kichrayadi.

    Uchta nuqta minimal talab: pastga, joyida, yuqoriga.
    """
    step = cfg.get("rounding_step") or DEFAULTS["rounding_step"]
    ladder = [g for g in (cfg.get("rounding_ladder") or DEFAULTS["rounding_ladder"]) if 0 < g <= step]
    if not ladder:
        ladder = [step]
    for grid in sorted(ladder, reverse=True):
        points = math.floor(hi_band / grid) - math.ceil(lo_band / grid) + 1
        if points >= 3:
            return grid
    return min(ladder)


def demand_score(stats: dict, cfg: dict, now_ts: float) -> tuple:
    """Talab ko'rsatkichi va unga bo'lgan ishonch.

    Returns:
        tuple: `(score, weight, v_recent, v_base)`.
        `score` — `log2` shkalasida: `+1` tezlik ikki barobar oshgani,
        `-1` — ikki barobar kamaygani. `weight` — 0..1 oralig'ida ishonch.
    """
    recent_days = max(1, int(cfg["recent_days"]))
    base_days = max(recent_days, int(cfg["baseline_days"]))

    qty_recent = max(0.0, float(stats.get("qty_recent") or 0.0))
    qty_base = max(0.0, float(stats.get("qty_base") or 0.0))

    # Tovar necha kundan beri mavjud — yangi tovarni "issiq" deb o'qimaslik uchun.
    # Usiz 7 kunlik tovarda v_b = qty/28, v_r = qty/7 bo'lib r≈4 chiqadi va
    # engine uni darrov qimmatlashtiradi. Bu real bug, soddalashtirmang.
    first_seen = stats.get("first_seen_ts")
    if first_seen:
        days_avail = _clamp((now_ts - float(first_seen)) / DAY, 1.0, float(base_days))
    else:
        days_avail = float(base_days)

    v_recent = qty_recent / min(float(recent_days), days_avail)
    v_base = qty_base / min(float(base_days), days_avail)

    # Prior — siyrak sotiladigan tovarni neytralga tortadi, shunda bitta
    # tasodifiy sotuv narxni sakratib yubormaydi.
    prior = max(
        float(cfg["prior_min"]),
        float(cfg["prior_alpha"]) * float(stats.get("course_median_velocity") or 0.0),
    )

    ratio = (v_recent + prior) / (v_base + prior)
    score = math.log2(ratio) if ratio > 0 else 0.0
    if abs(score) < float(cfg["deadband"]):
        score = 0.0

    # Ishonch — bu tovar haqida umuman qancha ma'lumotimiz bor degani.
    # Faqat `qty_recent` ga qarab bo'lmaydi: 28 kunda 600 dona sotilib, oxirgi
    # 7 kunda nol bo'lgan tovar — bu ENG ishonchli "talab qulagan" signali,
    # lekin qty_recent=0 bo'lgani uchun og'irlik nolga tushib, narx tushmay
    # qolardi. Shuning uchun bazaviy oynadagi hajm ham dalil sifatida olinadi
    # (yaqin oynaga keltirilgan holda).
    evidence = max(qty_recent, qty_base * float(recent_days) / float(base_days))
    weight = evidence / (evidence + float(cfg["confidence_units"]))
    return score, weight, v_recent, v_base


def compute_next(state: dict, stats: dict, cfg: dict, now_ts: float = None) -> dict:
    """Bitta tovar uchun keyingi narxni hisoblash. Sof funksiya — DB'ga tegmaydi.

    Args:
        state: `rate`, `base_rate`, `shadow_rate`, `last_engine_rate`,
            `cost_rate`, `locked`, `price_updated_ts`, `created_ts`, `excluded`.
        stats: `qty_recent`, `qty_base`, `first_seen_ts`,
            `course_median_velocity`.
        cfg: `merge_settings()` natijasi.
        now_ts: epoch sekund; sinovda qo'lda beriladi.

    Returns:
        dict: `applied_rate`, `shadow_rate`, `base_rate`, `last_engine_rate`,
        `status`, `reason`, `trend`, `changed`, `demand_score`, `weight`,
        `grid`, `min_rate`, `max_rate`, `velocity_recent`, `velocity_base`.
    """
    now_ts = time.time() if now_ts is None else float(now_ts)

    rate = float(state.get("rate") or 0.0)
    base = float(state.get("base_rate") or 0.0)
    shadow = float(state.get("shadow_rate") or 0.0)
    engine_rate = float(state.get("last_engine_rate") or 0.0)
    cost = float(state.get("cost_rate") or 0.0)

    def result(status, reason, applied=None, new_shadow=None, new_base=None,
               score=0.0, weight=0.0, grid=0, lo=0.0, hi=0.0,
               v_recent=0.0, v_base=0.0, engine=None):
        applied = rate if applied is None else applied
        new_base = base if new_base is None else new_base
        new_shadow = (shadow or applied) if new_shadow is None else new_shadow
        changed = abs(applied - rate) > 0.005
        if applied > rate + 0.005:
            trend = "up"
        elif applied < rate - 0.005:
            trend = "down"
        else:
            trend = state.get("trend") or "flat"
        return {
            "applied_rate": applied,
            "shadow_rate": new_shadow,
            "base_rate": new_base,
            "last_engine_rate": applied if engine is None else engine,
            "status": status,
            "reason": reason,
            "trend": trend,
            "changed": changed,
            "demand_score": score,
            "weight": weight,
            "grid": grid,
            "min_rate": lo,
            "max_rate": hi,
            "velocity_recent": v_recent,
            "velocity_base": v_base,
        }

    # ── 0. Yaroqsiz narx ──────────────────────────────────────────
    if rate <= 0:
        return result(STATUS_NO_BASE, "invalid_rate", applied=rate, new_shadow=rate)

    # ── 1. Bazaviy narx yo'q → shu narxni bazaviy deb qabul qilamiz ─
    if base <= 0:
        return result(STATUS_NO_BASE, "rebaseline", applied=rate,
                      new_shadow=rate, new_base=rate)

    # ── 2. Qulflangan / istisno qilingan ──────────────────────────
    if state.get("locked"):
        return result(STATUS_LOCKED, "locked", applied=rate, new_shadow=shadow or rate,
                      engine=engine_rate)
    if state.get("excluded"):
        return result(STATUS_EXCLUDED, "excluded", applied=rate, new_shadow=shadow or rate,
                      engine=engine_rate)

    # ── 3. Qo'lda tahrir qilinganmi? ──────────────────────────────
    # Admin Desk'da narxni o'zgartirgan bo'lsa, bu yangi bazaviy narx degani.
    # Usiz engine adminning tuzatishini darhol orqaga tortadi.
    if engine_rate > 0 and abs(rate - engine_rate) > 0.5:
        return result(STATUS_AUTO, "rebaseline", applied=rate,
                      new_shadow=rate, new_base=rate)

    # ── 4. Juda arzon tovar ───────────────────────────────────────
    if rate < float(cfg["min_price_for_dynamic"]):
        return result(STATUS_EXCLUDED_LOW_PRICE, "low_price", applied=rate,
                      new_shadow=rate, engine=engine_rate)

    # ── 5. Yangi tovar — kuzatuv muddati ──────────────────────────
    created = state.get("created_ts")
    if created and (now_ts - float(created)) < float(cfg["new_item_grace_days"]) * DAY:
        return result(STATUS_NEW, "grace_period", applied=rate, new_shadow=rate,
                      new_base=base if base > 0 else rate, engine=engine_rate)

    if shadow <= 0:
        shadow = rate

    # ── 6. Talab ──────────────────────────────────────────────────
    score, weight, v_recent, v_base = demand_score(stats, cfg, now_ts)

    # ── 7. Qadam: talab + o'rtaga qaytish ─────────────────────────
    # Ikkinchi had muhim: sotuvi to'xtagan tovarda weight→0 bo'ladi va narx
    # o'z-o'zidan bazaviyga qaytadi. Usiz bir marta qimmatlashgan tovar
    # abadiy qimmat qolardi.
    drift = shadow / base - 1.0
    delta = float(cfg["gain"]) * score * weight \
        - float(cfg["reversion"]) * (1.0 - weight) * drift

    # ── 8. Qadam chegarasi — o'tgan vaqtga bog'liq ────────────────
    # Soatlik sikllar kunlik chegaraga qo'shilib ketmasligi uchun: 24 ta
    # sikl × 2% = 48% bo'lib qolmasin.
    #
    # DIQQAT: bu yerda SOYA narx oxirgi marta qachon qimirlagani ishlatiladi,
    # sotuv narxi o'zgargan vaqt emas. Aks holda narxi hali o'zgarmagan tovar
    # har soatda to'liq 2% lik ruxsatni qayta olib, kuniga 48% siljirdi.
    shadow_ts = state.get("shadow_updated_ts")
    if shadow_ts:
        hours_shadow = (now_ts - float(shadow_ts)) / 3600.0
    else:
        hours_shadow = max(1.0, float(cfg["interval_minutes"])) / 60.0
    cap = min(
        float(cfg["max_step_pct_per_cycle"]),
        float(cfg["max_step_pct_per_day"]) * min(1.0, max(0.0, hours_shadow) / 24.0),
    ) / 100.0
    delta = _clamp(delta, -cap, cap)

    # Turish vaqti esa SOTUV narxi oxirgi o'zgarganidan beri hisoblanadi.
    price_ts = state.get("price_updated_ts")
    hours_price = ((now_ts - float(price_ts)) / 3600.0) if price_ts else 1e9

    shadow_next = shadow * (1.0 + delta)

    # ── 9. Koridor va tannarx poli ────────────────────────────────
    lo_band = base * (1.0 - float(cfg["max_down_pct"]) / 100.0)
    hi_band = base * (1.0 + float(cfg["max_up_pct"]) / 100.0)

    status = STATUS_AUTO
    lo = lo_band
    if cost > 0 and cfg["apply_cost_floor"]:
        floor = cost * (1.0 + float(cfg["min_margin_pct"]) / 100.0)
        if floor <= hi_band:
            lo = max(lo_band, floor)
        else:
            # Tannarx koridor shiftidan ham baland — bu bazaviy narx muammosi,
            # odam tuzatishi kerak. Narxni shiftda ushlab turamiz va belgilaymiz.
            status = STATUS_COST_VIOLATION
            shadow_next = hi_band
    # cost == 0 bo'lsa tannarx tekshiruvi umuman qo'llanilmaydi — bu ataylab:
    # tannarxi noma'lum tovarlar ham koridor ichida harakatlanaveradi.

    shadow_next = _clamp(shadow_next, lo, hi_band)

    # ── 10. Panjara va yaxlitlash ─────────────────────────────────
    grid = choose_grid(lo_band, hi_band, cfg)
    applied = grid * math.floor(shadow_next / grid + 0.5)

    # Tuzatish — yaxlitlash chegarani buzmasligi kafolati.
    if applied > hi_band:
        applied = grid * math.floor(hi_band / grid)
    if applied < lo:
        applied = grid * math.ceil(lo / grid)
    if applied > hi_band and lo > lo_band:
        # [lo, hi] oralig'iga bitta ham panjara nuqtasi tushmadi. Tannarxdan
        # past sotgandan ko'ra koridordan bir qadam chiqqan afzal.
        applied = grid * math.ceil(lo / grid)
        status = STATUS_COST_VIOLATION
    if applied <= 0:
        applied = rate

    # ── 11. Gisterezis — chegara atrofida chayqalishga qarshi ─────
    if abs(applied - rate) > 0.005:
        if abs(shadow_next - rate) < float(cfg["hysteresis_factor"]) * grid:
            applied = rate
        elif hours_price < float(cfg["min_change_interval_hours"]):
            applied = rate

    if status == STATUS_AUTO and weight <= 0.0 and abs(score) <= 0.0:
        status = STATUS_NO_DATA

    reason = "hold"
    if abs(applied - rate) > 0.005:
        reason = "demand_up" if applied > rate else "demand_down"
        if abs(score) <= 0.0:
            reason = "reversion"
        if status == STATUS_COST_VIOLATION:
            reason = "cost_floor"
        elif applied >= hi_band - 0.005 or applied <= lo + 0.005:
            reason = "band_clamp"

    return result(status, reason, applied=applied, new_shadow=shadow_next,
                  score=score, weight=weight, grid=grid, lo=lo, hi=hi_band,
                  v_recent=v_recent, v_base=v_base)


# ═══════════════════════════════════════════════════════════════════
#  Frappe qatlami
# ═══════════════════════════════════════════════════════════════════

def _frappe():
    """`frappe` ni kech import qilish — sof qism testda frappe'siz ishlaydi."""
    import frappe

    return frappe


def _emit_menu_change(branch: str, reason: str = "PRICING_UPDATED"):
    """Menyu o'zgardi signali — kech import (yuqoridagi sabab bilan).

    Narx `frappe.db.set_value(..., update_modified=False)` bilan yoziladi,
    ya'ni hech qanday hujjat hodisasi uyg'onmaydi. Ofitsant ilovasi menyuni
    xotirada saqlagani uchun signal SHU YERDAN yuborilishi shart —
    aks holda u eski narxni ko'rsatib turaveradi.
    """
    from ozturkapp.ozturkapp.utils.menu_realtime import emit_menu_change

    emit_menu_change(branch, reason)


def get_branch_settings(branch: str) -> dict:
    """Filial sozlamasi (`Branch.custom_dynamic_pricing`) + standart qiymatlar."""
    frappe = _frappe()
    raw = frappe.db.get_value("Branch", branch, "custom_dynamic_pricing")
    return merge_settings(raw)


def save_branch_settings(branch: str, settings: dict):
    frappe = _frappe()
    frappe.db.set_value("Branch", branch, "custom_dynamic_pricing",
                        json.dumps(settings, ensure_ascii=False))


def get_menu_for_branch(branch: str) -> str:
    frappe = _frappe()
    return (
        frappe.db.get_value("URY Menu", {"branch": branch, "enabled": 1}, "name")
        or frappe.db.get_value("URY Menu", {"branch": branch}, "name")
        or ""
    )


def get_price_list_for_menu(menu: str) -> str:
    """Menyuga bog'langan `Price List` — `sync_order` aynan shundan narx oladi."""
    frappe = _frappe()
    return (
        frappe.db.get_value("Price List", {"restaurant_menu": menu, "enabled": 1}, "name")
        or frappe.db.get_value("Price List", {"restaurant_menu": menu}, "name")
        or ""
    )


# ── Talab ────────────────────────────────────────────────────────

def get_demand(branch: str, cfg: dict, to_date=None) -> dict:
    """Filial bo'yicha har bir tovarning sotuv statistikasi.

    Returns:
        dict: `{item_code: {"qty_recent", "qty_base", "first_seen_ts"}}`.
    """
    if cfg.get("demand_source") == "synthetic":
        return _get_synthetic_demand(branch, cfg, to_date)

    frappe = _frappe()
    from frappe.utils import add_days, getdate

    to_date = getdate(to_date) if to_date else getdate()
    recent_from = add_days(to_date, -int(cfg["recent_days"]) + 1)
    base_from = add_days(to_date, -int(cfg["baseline_days"]) + 1)

    # `docstatus=1` bekor qilingan cheklarni allaqachon chiqaradi.
    # `is_return` filtrlanmaydi — qaytarilgan chekda qty manfiy, SUM to'g'ri ayiradi.
    rows = frappe.db.sql(
        """
        SELECT pii.item_code                                              AS item_code,
               SUM(CASE WHEN pi.posting_date >= %(recent_from)s
                        THEN pii.qty ELSE 0 END)                          AS qty_recent,
               SUM(pii.qty)                                               AS qty_base,
               MIN(pi.posting_date)                                       AS first_sold
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        WHERE pi.docstatus = 1
          AND pi.branch = %(branch)s
          AND pi.posting_date BETWEEN %(base_from)s AND %(to_date)s
        GROUP BY pii.item_code
        """,
        {
            "branch": branch,
            "base_from": base_from,
            "recent_from": recent_from,
            "to_date": to_date,
        },
        as_dict=True,
    )

    out = {}
    for row in rows:
        first_ts = None
        if row.get("first_sold"):
            first_ts = time.mktime(getdate(row["first_sold"]).timetuple())
        out[row["item_code"]] = {
            "qty_recent": float(row.get("qty_recent") or 0.0),
            "qty_base": float(row.get("qty_base") or 0.0),
            "first_seen_ts": first_ts,
        }
    return out


def _get_synthetic_demand(branch: str, cfg: dict, to_date=None) -> dict:
    """`seedPricingDemo` yaratgan sun'iy talabni o'qish (faqat dev muhitida).

    Dev serverda atigi bitta yakunlangan POS Invoice bor, shuning uchun real
    ma'lumot bilan sinash imkonsiz. Soxta POS Invoice yaratish esa GL va
    zaxirani buzadi — o'rniga bitta JSON hujjat ishlatiladi.
    """
    frappe = _frappe()
    from frappe.utils import add_days, getdate

    name = frappe.db.get_value(
        "URY Price Run", {"branch": branch, "mode": "seed"}, "settings_snapshot"
    )
    if not name:
        return {}
    try:
        payload = json.loads(name)
    except (TypeError, ValueError):
        return {}

    to_date = getdate(to_date) if to_date else getdate()
    recent_from = add_days(to_date, -int(cfg["recent_days"]) + 1)
    base_from = add_days(to_date, -int(cfg["baseline_days"]) + 1)

    out = {}
    for item_code, by_date in (payload.get("demand") or {}).items():
        qty_recent = qty_base = 0.0
        first = None
        for date_str, qty in by_date.items():
            day = getdate(date_str)
            if first is None or day < first:
                first = day
            if base_from <= day <= to_date:
                qty_base += float(qty)
                if day >= recent_from:
                    qty_recent += float(qty)
        out[item_code] = {
            "qty_recent": qty_recent,
            "qty_base": qty_base,
            "first_seen_ts": time.mktime(first.timetuple()) if first else None,
        }
    return out


def course_median_velocities(menu_rows: list, demand: dict, cfg: dict) -> dict:
    """Har bir kategoriya uchun median kunlik tezlik — prior sifatida ishlatiladi."""
    base_days = max(1, int(cfg["baseline_days"]))
    buckets = {}
    for row in menu_rows:
        stats = demand.get(row.get("item")) or {}
        velocity = float(stats.get("qty_base") or 0.0) / base_days
        buckets.setdefault(row.get("course") or "", []).append(velocity)

    out = {}
    for course, values in buckets.items():
        values.sort()
        mid = len(values) // 2
        out[course] = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0
    return out


# ── Tannarx ──────────────────────────────────────────────────────

def resolve_cost(item_code: str, cfg: dict, warehouse: str = None) -> tuple:
    """Tannarx — birinchi nolmas manba g'olib.

    Restoranda sotiladigan narsa tayyor taom, uning haqiqiy tannarxi retseptda
    (BOM). `Item.valuation_rate` esa faqat xomashyoda to'ldirilgan bo'ladi —
    shu sababli BOM zanjirning boshida turadi.

    Returns:
        tuple: `(cost, source)`. Topilmasa `(0.0, "none")` — bunda tannarx
        tekshiruvi umuman qo'llanilmaydi.
    """
    frappe = _frappe()
    from frappe.utils import flt

    for source in (cfg.get("cost_source_order") or DEFAULTS["cost_source_order"]):
        value = 0.0
        try:
            if source == "bom":
                bom = frappe.db.get_value(
                    "BOM",
                    {"item": item_code, "is_active": 1, "is_default": 1, "docstatus": 1},
                    ["total_cost", "quantity"],
                    as_dict=True,
                )
                if bom and flt(bom.quantity):
                    value = flt(bom.total_cost) / flt(bom.quantity)
            elif source == "bin" and warehouse:
                value = flt(frappe.db.get_value(
                    "Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate"
                ))
            elif source == "valuation_rate":
                value = flt(frappe.db.get_value("Item", item_code, "valuation_rate"))
            elif source == "last_purchase_rate":
                value = flt(frappe.db.get_value("Item", item_code, "last_purchase_rate"))
            elif source == "cogs":
                value = flt(frappe.db.get_value(
                    "URY Cost Of Goods", {"item_code": item_code}, "buying_price"
                ))
        except Exception:
            value = 0.0
        if value and value > 0:
            return flt(value), source

    return 0.0, "none"


# ── Sikl ─────────────────────────────────────────────────────────

def _cycle_key(branch: str, cfg: dict, now_ts: float) -> str:
    interval = max(1, int(cfg["interval_minutes"])) * 60
    return "{0}|{1}".format(branch, int(now_ts // interval))


def _within_apply_window(cfg: dict, now_dt) -> bool:
    """`apply_window` ("HH:MM-HH:MM") ichidamizmi. Bo'sh bo'lsa — har doim ha."""
    window = (cfg.get("apply_window") or "").strip()
    if not window or "-" not in window:
        return True
    try:
        start_s, end_s = window.split("-", 1)
        start_h, start_m = [int(x) for x in start_s.strip().split(":")]
        end_h, end_m = [int(x) for x in end_s.strip().split(":")]
    except (ValueError, IndexError):
        return True
    minutes = now_dt.hour * 60 + now_dt.minute
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    if start <= end:
        return start <= minutes <= end
    return minutes >= start or minutes <= end   # yarim tunni kesib o'tuvchi oyna


def run_for_branch(branch: str, mode: str = None, triggered_by: str = None,
                   force: bool = False) -> dict:
    """Filial uchun bitta narxlash sikli.

    Bir vaqtda bir necha kassa chaqirsa ham faqat bittasi bajariladi:
    `URY Price Run.run_key` unique, shuning uchun ikkinchi `insert()` xato beradi
    va biz `skipped` qaytaramiz. Redis yoki tashqi lock kerak emas.

    Args:
        mode: `None` → sozlamadagi `dry_run` ga qarab `dry`/`live`.
            `"manual"` — qo'lda ishga tushirish (sikl kaliti tekshirilmaydi).
        force: `True` bo'lsa `apply_window` va sikl kaliti e'tiborga olinmaydi.
    """
    frappe = _frappe()
    from frappe.utils import cint, flt, get_datetime, now_datetime

    started = time.time()
    cfg = get_branch_settings(branch)

    if not cint(cfg["enabled"]) and not force:
        return {"status": "skipped", "reason": "disabled", "branch": branch}

    now_dt = now_datetime()
    if not force and not _within_apply_window(cfg, now_dt):
        return {"status": "skipped", "reason": "outside_apply_window", "branch": branch}

    menu = get_menu_for_branch(branch)
    if not menu:
        return {"status": "skipped", "reason": "no_menu", "branch": branch}

    live = mode in ("live", "manual") or (mode is None and not cint(cfg["dry_run"]))
    if mode is None:
        mode = "live" if live else "dry"
    elif mode == "manual":
        live = not cint(cfg["dry_run"])

    # ── Idempotentlik: sikl kaliti ────────────────────────────────
    if force or mode == "manual":
        run_key = "{0}|manual|{1}".format(branch, frappe.generate_hash(length=10))
    else:
        run_key = _cycle_key(branch, cfg, started)

    try:
        run = frappe.get_doc({
            "doctype": "URY Price Run",
            "run_key": run_key,
            "branch": branch,
            "menu": menu,
            "mode": mode,
            "started_at": now_dt,
            "triggered_by": triggered_by or frappe.session.user,
            "settings_snapshot": json.dumps(cfg, ensure_ascii=False),
        }).insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        frappe.db.rollback()
        return {"status": "skipped", "reason": "already_ran_this_cycle",
                "run_key": run_key, "branch": branch}

    price_list = get_price_list_for_menu(menu)
    warehouse = frappe.db.get_value("POS Profile", {"branch": branch}, "warehouse")

    menu_rows = frappe.get_all(
        "URY Menu Item",
        filters={"parent": menu, "parenttype": "URY Menu", "disabled": 0},
        fields=[
            "name", "item", "item_name", "course", "rate", "creation",
            "custom_base_rate", "custom_shadow_rate", "custom_last_engine_rate",
            "custom_cost_rate", "custom_price_locked", "custom_price_updated_at",
            "custom_shadow_updated_at", "custom_trend",
        ],
    )

    demand = get_demand(branch, cfg)
    medians = course_median_velocities(menu_rows, demand, cfg)

    excluded_items = set(cfg.get("excluded_items") or [])
    excluded_courses = set(cfg.get("excluded_courses") or [])
    excluded_groups = set(cfg.get("excluded_item_groups") or [])
    group_map = {}
    if excluded_groups:
        for row in frappe.get_all("Item", filters={"name": ["in", [r.item for r in menu_rows]]},
                                  fields=["name", "item_group"]):
            group_map[row.name] = row.item_group

    counts = {"evaluated": 0, "shadow_moved": 0, "price_changed": 0, "locked": 0,
              "new": 0, "no_data": 0, "cost_violation": 0, "skipped_low_price": 0}
    changes = []
    today = now_dt.date()
    snapshot_needed = not frappe.db.exists(
        "URY Price Change Log",
        {"branch": branch, "entry_type": "snapshot", "posting_date": today},
    )

    for row in menu_rows:
        counts["evaluated"] += 1
        cost, cost_source = resolve_cost(row.item, cfg, warehouse)

        excluded = (
            row.item in excluded_items
            or (row.course or "") in excluded_courses
            or group_map.get(row.item) in excluded_groups
        )

        state = {
            "rate": flt(row.rate),
            "base_rate": flt(row.custom_base_rate),
            "shadow_rate": flt(row.custom_shadow_rate),
            "last_engine_rate": flt(row.custom_last_engine_rate),
            "cost_rate": cost,
            "locked": cint(row.custom_price_locked),
            "excluded": excluded,
            "trend": row.custom_trend,
            "created_ts": time.mktime(get_datetime(row.creation).timetuple()) if row.creation else None,
            "price_updated_ts": (
                time.mktime(get_datetime(row.custom_price_updated_at).timetuple())
                if row.custom_price_updated_at else None
            ),
            "shadow_updated_ts": (
                time.mktime(get_datetime(row.custom_shadow_updated_at).timetuple())
                if row.custom_shadow_updated_at else None
            ),
        }
        stats = dict(demand.get(row.item) or {})
        stats["course_median_velocity"] = medians.get(row.course or "", 0.0)

        decision = compute_next(state, stats, cfg, started)

        status = decision["status"]
        if status == STATUS_LOCKED:
            counts["locked"] += 1
        elif status == STATUS_NEW:
            counts["new"] += 1
        elif status == STATUS_NO_DATA:
            counts["no_data"] += 1
        elif status == STATUS_COST_VIOLATION:
            counts["cost_violation"] += 1
        elif status == STATUS_EXCLUDED_LOW_PRICE:
            counts["skipped_low_price"] += 1

        _persist_item(row, decision, cost, cost_source, live, cfg, now_dt, counts)

        if decision["changed"]:
            changes.append({
                "item": row.item, "item_name": row.item_name,
                "from": flt(row.rate), "to": decision["applied_rate"],
                "shadow": decision["shadow_rate"], "score": decision["demand_score"],
                "reason": decision["reason"],
            })
            _log_change(run.name, branch, menu, row, decision, cost, stats,
                        "change", live, today)

        if snapshot_needed:
            _log_change(run.name, branch, menu, row, decision, cost, stats,
                        "snapshot", live, today)

        if live and decision["changed"]:
            _apply_price(row, decision, menu, price_list)

    version = 0
    if live and counts["price_changed"]:
        frappe.db.sql(
            "UPDATE `tabBranch` SET custom_pricing_version = IFNULL(custom_pricing_version, 0) + 1 "
            "WHERE name = %s",
            branch,
        )
        version = cint(frappe.db.get_value("Branch", branch, "custom_pricing_version"))

    from frappe.utils import add_to_date
    frappe.db.set_value("Branch", branch, {
        "custom_pricing_last_run": now_dt,
        "custom_pricing_next_run": add_to_date(now_dt, minutes=int(cfg["interval_minutes"])),
    }, update_modified=False)

    run.db_set({
        "finished_at": now_datetime(),
        "pricing_version": version,
        "duration_ms": int((time.time() - started) * 1000),
        "evaluated": counts["evaluated"],
        "shadow_moved": counts["shadow_moved"],
        "price_changed": counts["price_changed"],
        "locked_count": counts["locked"],
        "new_count": counts["new"],
        "no_data_count": counts["no_data"],
        "cost_violation_count": counts["cost_violation"],
        "skipped_count": counts["skipped_low_price"],
    }, update_modified=False)

    frappe.db.commit()

    if live and counts["price_changed"]:
        frappe.publish_realtime(
            "pricing_updated",
            {"branch": branch, "pricing_version": version},
            after_commit=True,
        )
        # Ofitsant ilovasi menyuni xotirada saqlaydi — narx o'zgargani
        # haqida ALOHIDA xabar kerak (`pricing_updated` ni Desktop POS
        # tinglaydi, ilova esa yagona "menyu o'zgardi" kanalini).
        _emit_menu_change(branch)

    return {
        "status": "ok", "run": run.name, "mode": mode, "branch": branch, "menu": menu,
        "pricing_version": version, "counts": counts, "changes": changes[:200],
        "duration_ms": int((time.time() - started) * 1000),
    }


def _persist_item(row, decision, cost, cost_source, live, cfg, now_dt, counts):
    """Hisoblash natijasini `URY Menu Item` ga yozish (narxdan tashqari)."""
    frappe = _frappe()
    from frappe.utils import cint, flt

    values = {
        "custom_base_rate": decision["base_rate"],
        "custom_cost_rate": cost,
        "custom_cost_source": cost_source,
        "custom_cost_updated": now_dt,
        "custom_demand_score": decision["demand_score"],
        "custom_trend": decision["trend"],
        "custom_pricing_status": decision["status"],
    }

    # Dry-run da soya narx harakatlanadi (traektoriyani ko'rish uchun), lekin
    # sotuv narxi tegilmaydi. `savePricingSettings` dry→live o'tishida soyani
    # majburan tenglashtiradi, aks holda to'plangan farq bir zumda qo'llanardi.
    if live or cint(cfg["dry_run_persists_shadow"]):
        if abs(flt(row.custom_shadow_rate) - decision["shadow_rate"]) > 0.0005:
            counts["shadow_moved"] += 1
        values["custom_shadow_rate"] = decision["shadow_rate"]
        values["custom_shadow_updated_at"] = now_dt

    # `price_changed` dry-run da ham sanaladi — aks holda "nima o'zgargan bo'lardi"
    # degan savolga hisobot javob bera olmasdi. Haqiqiy yozuv esa `live` bilan.
    if decision["changed"]:
        counts["price_changed"] += 1

    if live and decision["changed"]:
        values["custom_last_engine_rate"] = decision["last_engine_rate"]
        values["custom_price_updated_at"] = now_dt
    elif decision["reason"] == "rebaseline":
        values["custom_last_engine_rate"] = decision["last_engine_rate"]

    frappe.db.set_value("URY Menu Item", row.name, values, update_modified=False)


def _apply_price(row, decision, menu: str, price_list: str):
    """Yangi narxni IKKALA joyga yozish — menyu qatori va Item Price.

    `URY Menu Item.rate` — POS ko'radigan narx.
    `Item Price.price_list_rate` — `sync_order` invoysni shundan narxlaydi.
    Faqat bittasini yozish chek va invoys summasining farq qilishiga olib keladi.
    """
    frappe = _frappe()
    rate = decision["applied_rate"]

    frappe.db.set_value("URY Menu Item", row.name, "rate", rate, update_modified=False)

    if not price_list:
        return
    existing = frappe.db.get_value(
        "Item Price", {"item_code": row.item, "price_list": price_list}, "name"
    )
    if existing:
        frappe.db.set_value("Item Price", existing, "price_list_rate", rate,
                            update_modified=False)
    else:
        frappe.get_doc({
            "doctype": "Item Price",
            "price_list": price_list,
            "item_code": row.item,
            "price_list_rate": rate,
        }).insert(ignore_permissions=True)


def _log_change(run_name, branch, menu, row, decision, cost, stats,
                entry_type, applied, posting_date):
    """Audit yozuvi. `change` — har o'zgarishda, `snapshot` — kuniga bir marta.

    Hajm nazorati muhim: 465 tovar × 24 sikl = kuniga 11 000 qator bo'lardi.
    Shuning uchun faqat haqiqiy o'zgarish va kunlik surat yoziladi.
    """
    frappe = _frappe()
    from frappe.utils import flt

    frappe.get_doc({
        "doctype": "URY Price Change Log",
        "run": run_name,
        "branch": branch,
        "menu": menu,
        "item": row.item,
        "entry_type": entry_type,
        "posting_date": posting_date,
        "old_rate": flt(row.rate),
        "new_rate": decision["applied_rate"],
        "old_shadow": flt(row.custom_shadow_rate),
        "new_shadow": decision["shadow_rate"],
        "base_rate": decision["base_rate"],
        "cost_rate": cost,
        "min_rate": decision["min_rate"],
        "max_rate": decision["max_rate"],
        "grid": decision["grid"],
        "demand_score": decision["demand_score"],
        "qty_recent": flt(stats.get("qty_recent")),
        "qty_base": flt(stats.get("qty_base")),
        "reason": decision["reason"] if entry_type == "change" else "snapshot",
        "applied": 1 if applied else 0,
        "user": frappe.session.user,
    }).insert(ignore_permissions=True)


def run_due_branches():
    """Scheduler kirish nuqtasi — vaqti kelgan filiallarni hisoblash.

    `hooks.py` da har 5 daqiqada chaqiriladi; qaysi filial haqiqatan
    hisoblanishi `interval_minutes` va sikl kaliti bilan belgilanadi.
    """
    frappe = _frappe()

    for branch in frappe.get_all("Branch", pluck="name"):
        cfg = get_branch_settings(branch)
        if not cfg.get("enabled"):
            continue
        try:
            run_for_branch(branch)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title="URY dinamik narxlash xatosi: {0}".format(branch),
                message=frappe.get_traceback(),
            )


def revert_to_base(branch: str, triggered_by: str = None) -> dict:
    """PANIK TUGMA — barcha narxlarni bazaviy narxga qaytarish.

    Go-live'dan oldin bu ishlashiga ishonch hosil qiling.
    """
    frappe = _frappe()
    from frappe.utils import cint, flt, now_datetime

    started = time.time()
    now_dt = now_datetime()
    menu = get_menu_for_branch(branch)
    if not menu:
        return {"status": "skipped", "reason": "no_menu", "branch": branch}

    price_list = get_price_list_for_menu(menu)
    run = frappe.get_doc({
        "doctype": "URY Price Run",
        "run_key": "{0}|revert|{1}".format(branch, frappe.generate_hash(length=10)),
        "branch": branch, "menu": menu, "mode": "revert",
        "started_at": now_dt,
        "triggered_by": triggered_by or frappe.session.user,
    }).insert(ignore_permissions=True)

    reverted = 0
    for row in frappe.get_all(
        "URY Menu Item",
        filters={"parent": menu, "parenttype": "URY Menu"},
        fields=["name", "item", "item_name", "rate", "custom_base_rate",
                "custom_shadow_rate"],
    ):
        base = flt(row.custom_base_rate)
        if base <= 0 or abs(base - flt(row.rate)) < 0.005:
            continue
        decision = {
            "applied_rate": base, "shadow_rate": base, "base_rate": base,
            "last_engine_rate": base, "demand_score": 0.0, "grid": 0,
            "min_rate": base, "max_rate": base, "reason": "revert",
            "status": STATUS_AUTO, "changed": True, "trend": "flat",
        }
        _apply_price(row, decision, menu, price_list)
        frappe.db.set_value("URY Menu Item", row.name, {
            "custom_shadow_rate": base,
            "custom_last_engine_rate": base,
            "custom_price_updated_at": now_dt,
            "custom_trend": "flat",
        }, update_modified=False)
        _log_change(run.name, branch, menu, row, decision, 0.0, {},
                    "revert", True, now_dt.date())
        reverted += 1

    frappe.db.sql(
        "UPDATE `tabBranch` SET custom_pricing_version = IFNULL(custom_pricing_version, 0) + 1 "
        "WHERE name = %s", branch,
    )
    version = cint(frappe.db.get_value("Branch", branch, "custom_pricing_version"))
    run.db_set({"finished_at": now_datetime(), "price_changed": reverted,
                "pricing_version": version,
                "duration_ms": int((time.time() - started) * 1000)},
               update_modified=False)
    frappe.db.commit()
    frappe.publish_realtime("pricing_updated",
                            {"branch": branch, "pricing_version": version},
                            after_commit=True)
    _emit_menu_change(branch)

    return {"status": "ok", "run": run.name, "reverted": reverted,
            "pricing_version": version, "branch": branch}


def resync_shadow(branch: str) -> int:
    """Soya narxni sotuv narxiga tenglashtirish (dry → live o'tishida).

    Dry-run davomida soya narx harakatlanadi, sotuv narxi esa turadi. To'g'ridan
    live'ga o'tilsa, to'plangan butun farq bir zumda qo'llanib, narx keskin
    sakrab ketardi.
    """
    frappe = _frappe()

    menu = get_menu_for_branch(branch)
    if not menu:
        return 0
    frappe.db.sql(
        """UPDATE `tabURY Menu Item`
           SET custom_shadow_rate = rate, custom_last_engine_rate = rate
           WHERE parent = %s AND parenttype = 'URY Menu'""",
        menu,
    )
    frappe.db.commit()
    return frappe.db.sql(
        """SELECT COUNT(*) FROM `tabURY Menu Item`
           WHERE parent = %s AND parenttype = 'URY Menu'""",
        menu,
    )[0][0]


def prune_price_logs():
    """Eski audit yozuvlarini tozalash (kuniga bir marta, `hooks.py`)."""
    frappe = _frappe()
    from frappe.utils import add_days, nowdate

    days = int(DEFAULTS["retention_days"])
    cutoff = add_days(nowdate(), -days)
    frappe.db.sql("DELETE FROM `tabURY Price Change Log` WHERE posting_date < %s", cutoff)
    frappe.db.commit()
