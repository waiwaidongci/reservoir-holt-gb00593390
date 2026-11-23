import unittest
from datetime import date

from src.domain.patrol import (OFFICE, Network, PatrolError, Pool, plan_route,
                               shift_bounds, at_minute)

D = date(2026, 11, 23)


def net(edges, blocked=()):
    return Network(travel={tuple(sorted(k)): v for k, v in edges.items()},
                   blocked={tuple(sorted(k)) for k in blocked})


def pool(code, trigger, dwell=10):
    return Pool(code=code, name=f"池{code}", trigger_date=trigger, dwell_min=dwell)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        # 值班室到各池及池间路段（分钟）。
        self.net = net({
            (OFFICE, "A"): 10, (OFFICE, "B"): 20, (OFFICE, "C"): 30,
            ("A", "B"): 15, ("B", "C"): 15, ("A", "C"): 40,
        })
        self.pools = [
            pool("A", date(2026, 11, 24), 10),
            pool("B", date(2026, 11, 23), 10),  # 今天触线：本班已无法触线前完成
            pool("C", date(2026, 11, 25), 10),
        ]

    def test_basic_route_returns_to_office_within_shift(self):
        start, end = 8 * 60, 12 * 60
        out = plan_route(self.pools, self.net, start, end, D)
        self.assertEqual(out["legs"][0]["code"], out["order"][0])
        self.assertEqual(out["legs"][-1]["code"], OFFICE)
        self.assertLessEqual(out["back_min"], end)
        self.assertTrue(out["feasible"])
        # B 触线日就是班次当天，不计入触线前完成；A、C 计入。
        self.assertEqual(out["on_time_count"], 2)

    def test_max_on_time_beats_short_total(self):
        # 近池 X 已无法按时；远池 Y 仍可按时。按时池数优先于总用时。
        n = net({(OFFICE, "X"): 5, (OFFICE, "Y"): 40, ("X", "Y"): 40})
        pools = [pool("X", D, 5), pool("Y", date(2026, 11, 24), 5)]
        start, end = 8 * 60, 9 * 60 + 30
        out = plan_route(pools, n, start, end, D)
        self.assertIn("Y", out["order"])
        self.assertEqual(out["on_time_count"], 1)

    def test_tie_prefers_shorter_total_then_lexicographic(self):
        n = net({(OFFICE, "A"): 10, (OFFICE, "B"): 10, ("A", "B"): 100})
        pools = [pool("A", date(2026, 11, 24), 10),
                 pool("B", date(2026, 11, 24), 10)]
        start, end = 8 * 60, 12 * 60
        out = plan_route(pools, n, start, end, D)
        # 两池都按时；A→B 跨池 100 分钟，B→A 同样。总用时相同则字典序 A 在前。
        self.assertEqual(out["order"], ["A", "B"])

    def test_blocked_edge_is_never_used(self):
        blocked = Network(travel=dict(self.net.travel),
                          blocked={tuple(sorted((OFFICE, "C")))})
        start, end = 8 * 60, 12 * 60
        out = plan_route(self.pools, blocked, start, end, D)
        # 首站不能直接是 C（值班室→C 封路），但允许经 B 巡 C。
        self.assertNotEqual(out["legs"][0]["code"], "C")
        self.assertIn("C", out["order"])

    def test_full_blockade_raises(self):
        n = net({(OFFICE, "A"): 10}, blocked=[(OFFICE, "A")])
        with self.assertRaises(PatrolError):
            plan_route([pool("A", date(2026, 11, 24))], n, 8 * 60, 9 * 60, D)

    def test_shift_too_short_to_return_raises(self):
        with self.assertRaises(PatrolError):
            plan_route(self.pools, self.net, 8 * 60, 8 * 60 + 5, D)

    def test_replan_fixes_completed_prefix(self):
        start, end = 8 * 60, 12 * 60
        # 已巡完 A，从 A 在 08:40 接续；固定前缀 A 必须出现在最前且不被重排。
        out = plan_route(self.pools, self.net, start, end, D,
                         start="A", start_minute=8 * 60 + 40, fixed_prefix=["A"])
        self.assertEqual(out["order"][0], "A")
        self.assertEqual(out["legs"][0]["code"], "A")
        self.assertNotIn("A", out["order"][1:])
        self.assertEqual(out["legs"][-1]["code"], OFFICE)

    def test_replan_blockade_on_fixed_prefix_raises(self):
        n = Network(travel=dict(self.net.travel),
                    blocked={tuple(sorted(("A", "B")))})
        with self.assertRaises(PatrolError):
            plan_route(self.pools, n, 8 * 60, 12 * 60, D,
                       start="A", start_minute=8 * 60 + 40, fixed_prefix=["A", "B"])

    def test_in_progress_pool_is_excluded_from_replan(self):
        # 人已到达 B 但尚未离开：从 B 接续，B 不重复排入待巡序列，路线仍须回值班室。
        out = plan_route(self.pools, self.net, 8 * 60, 12 * 60, D,
                         start="B", start_minute=9 * 60, fixed_prefix=["A"],
                         exclude_codes=["B"])
        self.assertNotIn("B", out["order"][1:])
        self.assertEqual(out["order"][0], "A")
        self.assertIn("B", out["in_progress"])
        self.assertEqual(out["legs"][-1]["code"], OFFICE)

    def test_late_pool_still_covered_when_on_time_tied(self):
        # 按时池数相同时，覆盖更多池的路线胜出；仅当时间真的不够才跳过。
        n = net({(OFFICE, "LATE"): 5, (OFFICE, "FRESH"): 5, ("LATE", "FRESH"): 10})
        pools = [pool("LATE", D, 5), pool("FRESH", date(2026, 11, 24), 5)]
        out = plan_route(pools, n, 8 * 60, 12 * 60, D)
        self.assertEqual(sorted(out["order"]), ["FRESH", "LATE"])
        self.assertEqual(out["on_time_count"], 1)   # 只有 FRESH 触线前完成
        self.assertEqual(out["coverage_count"], 2)  # LATE 仍被巡到

    def test_forecast_without_trigger_counts_as_on_time(self):
        pools = [pool("Z", None, 10)]
        n = net({(OFFICE, "Z"): 10})
        out = plan_route(pools, n, 8 * 60, 9 * 60, D)
        self.assertEqual(out["on_time_count"], 1)
        self.assertTrue(out["legs"][0]["on_time"])

    def test_shift_bounds_validation(self):
        with self.assertRaises(PatrolError):
            shift_bounds(D, __import__("datetime").time(18), __import__("datetime").time(9))

    def test_at_minute_roundtrip(self):
        dt = at_minute(D, 9 * 60 + 30)
        self.assertEqual((dt.hour, dt.minute), (9, 30))


if __name__ == "__main__":
    unittest.main()
