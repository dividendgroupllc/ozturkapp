"""Dinamik narxlash engine testlari — frappe'siz, sof funksiyalar ustida.

Ishga tushirish:
    cd ~/frappe-bench/apps/ury
    ~/frappe-bench/env/bin/python -m unittest ozturkapp.ozturkapp.tests.test_dynamic_pricing -v

Bu testlar DB'ga ham, frappe'ga ham tegmaydi (`compute_next` sof funksiya),
shuning uchun bench ishlab turmasa ham yugurtirish mumkin. `unittest` ataylab
tanlangan — frappe'ning o'zi ham shuni ishlatadi, qo'shimcha bog'liqlik kerak emas.
"""

import unittest

from ozturkapp.ozturkapp.api.dynamic_pricing import (
    DEFAULTS,
    STATUS_COST_VIOLATION,
    STATUS_EXCLUDED_LOW_PRICE,
    STATUS_NEW,
    choose_grid,
    compute_next,
    merge_settings,
)

import math

DAY = 86400.0
T0 = 1_800_000_000.0          # barqaror boshlang'ich vaqt (testlar takrorlanuvchi bo'lsin)
CYCLES_PER_DAY = 24


def cfg(**overrides) -> dict:
    settings = merge_settings({"enabled": 1, "dry_run": 0})
    settings.update(overrides)
    return settings


def state(rate, base=None, shadow=None, cost=0.0, **extra) -> dict:
    base = rate if base is None else base
    st = {
        "rate": rate,
        "base_rate": base,
        "shadow_rate": rate if shadow is None else shadow,
        "last_engine_rate": rate,
        "cost_rate": cost,
        "locked": 0,
        "excluded": False,
        "trend": "flat",
        "created_ts": T0 - 400 * DAY,      # kuzatuv muddati allaqachon tugagan
        "price_updated_ts": T0 - 30 * DAY,
        "shadow_updated_ts": None,
    }
    st.update(extra)
    return st


def simulate(st, demand_fn, settings, cycles, start_ts=T0):
    """Engine'ni ketma-ket sikllarda yugurtirish.

    Holat ko'chishi `run_for_branch` / `_persist_item` bilan bir xil bo'lishi
    shart — aks holda test haqiqiy xatti-harakatni tekshirmaydi.
    """
    st = dict(st)
    interval = max(1, int(settings["interval_minutes"])) * 60
    out = []
    for i in range(cycles):
        now = start_ts + i * interval
        decision = compute_next(st, demand_fn(i, now), settings, now)

        st["base_rate"] = decision["base_rate"]
        st["shadow_rate"] = decision["shadow_rate"]
        st["shadow_updated_ts"] = now
        st["trend"] = decision["trend"]
        if decision["changed"]:
            st["rate"] = decision["applied_rate"]
            st["last_engine_rate"] = decision["last_engine_rate"]
            st["price_updated_ts"] = now
        out.append(decision)
    return st, out


def steady(qty_recent, qty_base, median=4.0, first_seen=T0 - 400 * DAY):
    """O'zgarmas talab generatori."""
    def gen(_i, _now):
        return {"qty_recent": qty_recent, "qty_base": qty_base,
                "first_seen_ts": first_seen, "course_median_velocity": median}
    return gen


def from_daily(daily_fn, settings, median=4.0, start_ts=T0):
    """Kunlik sotuvdan 7/28 kunlik yig'indi yasovchi generator.

    Server `get_demand()` da aynan shunday — siljuvchi yig'indi — hisoblaydi.
    Bu muhim: 7 kunlik yig'indi kundan kunga bir necha barobar sakray olmaydi,
    shuning uchun "talab har kuni 4× o'zgardi" degan test fizik jihatdan
    imkonsiz holatni tekshirgan bo'lardi.
    """
    recent = int(settings["recent_days"])
    base = int(settings["baseline_days"])

    def gen(_i, now):
        day = int((now - start_ts) // DAY)
        return {
            "qty_recent": sum(daily_fn(day - k) for k in range(recent)),
            "qty_base": sum(daily_fn(day - k) for k in range(base)),
            "first_seen_ts": start_ts - 400 * DAY,
            "course_median_velocity": median,
        }
    return gen


class GridTest(unittest.TestCase):
    """Yaxlitlash panjarasi."""

    def test_grid_always_leaves_at_least_three_points(self):
        """Koridorda kamida 3 nuqta bo'lmasa, tovar konstruksiya bo'yicha muzlaydi."""
        settings = cfg()
        for base in (2000, 3000, 5000, 9000, 10000, 14000, 25000, 155000):
            with self.subTest(base=base):
                lo, hi = base * 0.9, base * 1.1
                grid = choose_grid(lo, hi, settings)
                points = math.floor(hi / grid) - math.ceil(lo / grid) + 1
                self.assertGreaterEqual(points, 3, "narx {0}, panjara {1}".format(base, grid))

    def test_grid_shrinks_for_cheap_items(self):
        settings = cfg()
        self.assertEqual(choose_grid(1350, 1650, settings), 100)
        self.assertEqual(choose_grid(9000, 11000, settings), 500)


class FreezeRegressionTest(unittest.TestCase):
    """Reja tuzilishida aniqlangan asosiy xavf — yaxlitlash tuzog'i."""

    def test_hot_item_is_not_frozen_by_rounding(self):
        """9 000 UZS tovar 2× talab bilan bir haftada 9 500 ga chiqishi shart.

        Soya narxsiz variantda 2% qadam (180 UZS) 500 lik panjarada har safar
        9 000 ga qaytib, narx abadiy qotib qolardi.
        """
        final, history = simulate(
            state(9000), steady(qty_recent=140, qty_base=280), cfg(),
            cycles=7 * CYCLES_PER_DAY,
        )
        self.assertEqual(final["rate"], 9500, "narx ko'tarilmadi")
        self.assertTrue(any(d["changed"] for d in history))

    def test_cheap_item_is_not_frozen(self):
        """Arzon tovar panjara kichrayishi hisobiga qimirlay olsin."""
        final, _ = simulate(
            state(1500), steady(qty_recent=200, qty_base=400),
            cfg(min_price_for_dynamic=1000), cycles=14 * CYCLES_PER_DAY,
        )
        self.assertGreater(final["rate"], 1500)


class BoundsTest(unittest.TestCase):
    """Chegaralar har siklda buzilmasligi shart."""

    def test_shadow_and_price_never_leave_the_band(self):
        base = 20000
        lo, hi = base * 0.9, base * 1.1
        for qty_recent, qty_base in ((400, 500), (5, 400), (0, 0), (140, 280)):
            with self.subTest(qty_recent=qty_recent, qty_base=qty_base):
                _, history = simulate(
                    state(base), steady(qty_recent, qty_base), cfg(),
                    cycles=30 * CYCLES_PER_DAY,
                )
                for i, d in enumerate(history):
                    self.assertGreaterEqual(d["shadow_rate"], lo - 0.01, "sikl {0}".format(i))
                    self.assertLessEqual(d["shadow_rate"], hi + 0.01, "sikl {0}".format(i))
                    self.assertGreaterEqual(d["applied_rate"], lo - 0.01, "sikl {0}".format(i))
                    self.assertLessEqual(d["applied_rate"], hi + 0.01, "sikl {0}".format(i))

    def test_applied_price_always_lands_on_the_grid(self):
        _, history = simulate(
            state(20000), steady(400, 500), cfg(), cycles=20 * CYCLES_PER_DAY,
        )
        for d in history:
            self.assertEqual(d["applied_rate"] % d["grid"], 0)

    def test_daily_step_limit_is_respected(self):
        """24 ta soatlik sikl kunlik chegarani oshirib yubormasin."""
        settings = cfg()
        _, history = simulate(
            state(100000), steady(500, 2000), settings, cycles=CYCLES_PER_DAY,
        )
        ceiling = 100000 * (1 + settings["max_step_pct_per_day"] / 100.0) + 1
        self.assertLessEqual(history[-1]["shadow_rate"], ceiling)


class CostFloorTest(unittest.TestCase):
    """Tannarx poli."""

    def test_cost_floor_is_never_breached_by_rounding(self):
        cost = 19000
        floor = cost * 1.005
        _, history = simulate(
            state(20000, cost=cost),
            steady(qty_recent=0, qty_base=600),      # talab tushgan — narx pastga intiladi
            cfg(min_margin_pct=0.5), cycles=30 * CYCLES_PER_DAY,
        )
        for i, d in enumerate(history):
            self.assertGreaterEqual(d["applied_rate"], floor - 0.01, "sikl {0}".format(i))

    def test_unknown_cost_skips_the_floor_check_but_keeps_the_band(self):
        """Tannarx noma'lum — tekshiruv yo'q, lekin koridor baribir ushlab turadi."""
        base = 20000
        final, _ = simulate(
            state(base, cost=0.0), steady(qty_recent=0, qty_base=600), cfg(),
            cycles=40 * CYCLES_PER_DAY,
        )
        self.assertLess(final["rate"], base)
        self.assertGreaterEqual(final["rate"], base * 0.9 - 0.01)

    def test_cost_above_ceiling_pins_at_the_top_and_flags(self):
        base = 20000
        _, history = simulate(
            state(base, cost=23000), steady(100, 400), cfg(),
            cycles=3 * CYCLES_PER_DAY,
        )
        last = history[-1]
        self.assertEqual(last["status"], STATUS_COST_VIOLATION)
        self.assertLessEqual(last["applied_rate"], base * 1.1 + 0.01)


class StabilityTest(unittest.TestCase):
    """Barqarorlik — mijoz uchun narx sakramasin."""

    def test_no_demand_converges_back_to_base(self):
        base = 12000
        final, _ = simulate(
            state(12500, base=base, shadow=12500),
            steady(qty_recent=0, qty_base=0, median=0.0), cfg(),
            cycles=14 * CYCLES_PER_DAY,
        )
        self.assertEqual(final["rate"], base)

    def test_no_two_changes_within_the_dwell_window(self):
        """Qattiq kafolat: narx `min_change_interval_hours` dan tez o'zgarmaydi.

        Kassir uchun eng ko'rinadigan xususiyat — javon narxi kun davomida
        necha marta almashishi.
        """
        settings = cfg(min_change_interval_hours=6)
        for daily in (lambda d: 30, lambda d: 5, lambda d: 40 if d % 3 else 5):
            with self.subTest(daily=daily(0)):
                _, history = simulate(
                    state(20000), from_daily(daily, settings), settings,
                    cycles=21 * CYCLES_PER_DAY,
                )
                changes = [i for i, d in enumerate(history) if d["changed"]]
                for earlier, later in zip(changes, changes[1:]):
                    self.assertGreaterEqual(later - earlier, 6)

    def test_stable_demand_settles_to_a_fixed_price(self):
        """Talab barqaror bo'lsa narx bir joyda to'xtashi kerak — cheksiz sudralmasin."""
        settings = cfg()
        _, history = simulate(
            state(20000), from_daily(lambda d: 25, settings), settings,
            cycles=40 * CYCLES_PER_DAY,
        )
        tail = history[-3 * CYCLES_PER_DAY:]      # oxirgi 3 kun
        self.assertEqual(sum(1 for d in tail if d["changed"]), 0)

    def test_demand_collapse_pushes_the_price_down(self):
        """Sotuvi to'xtagan tovar arzonlashishi shart — talabning o'zagi shu."""
        settings = cfg()

        def daily(day):
            return 30 if day < 0 else 0          # bugundan boshlab umuman sotilmadi

        final, _ = simulate(
            state(20000), from_daily(daily, settings), settings,
            cycles=21 * CYCLES_PER_DAY,
        )
        self.assertLess(final["rate"], 20000)
        self.assertGreaterEqual(final["rate"], 20000 * 0.9 - 0.01)

    def test_demand_surge_pushes_the_price_up(self):
        """Sotuvi keskin oshgan tovar qimmatlashishi shart."""
        settings = cfg()

        def daily(day):
            return 10 if day < 0 else 40

        final, _ = simulate(
            state(20000), from_daily(daily, settings), settings,
            cycles=21 * CYCLES_PER_DAY,
        )
        self.assertGreater(final["rate"], 20000)
        self.assertLessEqual(final["rate"], 20000 * 1.1 + 0.01)


class EdgeCaseTest(unittest.TestCase):
    """Maxsus holatlar."""

    def test_new_item_is_left_alone_during_grace_period(self):
        st = state(15000)
        st["created_ts"] = T0 - 3 * DAY
        decision = compute_next(st, steady(60, 60)(0, T0), cfg(), T0)
        self.assertEqual(decision["status"], STATUS_NEW)
        self.assertEqual(decision["applied_rate"], 15000)

    def test_new_item_does_not_look_hot_after_grace(self):
        """`days_avail` tuzatishisiz 7 kunlik tovar r≈4 berib qimmatlashardi."""
        born = T0 - 7 * DAY
        st = state(20000)
        st["created_ts"] = born
        stats = {"qty_recent": 60, "qty_base": 60,     # butun sotuv oxirgi 7 kunda
                 "first_seen_ts": born, "course_median_velocity": 4.0}
        decision = compute_next(st, stats, cfg(new_item_grace_days=0), T0)
        self.assertAlmostEqual(decision["demand_score"], 0.0, places=9)

    def test_manual_edit_rebaselines(self):
        """Admin Desk'da narxni o'zgartirsa, u yangi bazaviy narx bo'ladi."""
        st = state(20000)
        st["rate"] = 26000                 # admin qo'lda ko'tardi
        st["last_engine_rate"] = 20000     # engine esa 20 000 ni yozgan edi
        decision = compute_next(st, steady(0, 0)(0, T0), cfg(), T0)
        self.assertEqual(decision["reason"], "rebaseline")
        self.assertEqual(decision["base_rate"], 26000)
        self.assertEqual(decision["applied_rate"], 26000)

    def test_locked_item_is_untouched(self):
        decision = compute_next(state(20000, locked=1), steady(500, 100)(0, T0), cfg(), T0)
        self.assertEqual(decision["applied_rate"], 20000)
        self.assertFalse(decision["changed"])

    def test_low_price_item_is_excluded(self):
        decision = compute_next(state(800), steady(500, 100)(0, T0), cfg(), T0)
        self.assertEqual(decision["status"], STATUS_EXCLUDED_LOW_PRICE)
        self.assertEqual(decision["applied_rate"], 800)

    def test_missing_base_rate_adopts_current_price(self):
        decision = compute_next(state(20000, base=0.0), steady(0, 0)(0, T0), cfg(), T0)
        self.assertEqual(decision["base_rate"], 20000)
        self.assertEqual(decision["shadow_rate"], 20000)

    def test_broken_settings_fall_back_to_defaults(self):
        self.assertEqual(merge_settings("not json")["max_up_pct"], DEFAULTS["max_up_pct"])
        self.assertEqual(merge_settings(None)["gain"], DEFAULTS["gain"])
        self.assertEqual(merge_settings({"max_up_pct": 25})["max_up_pct"], 25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
