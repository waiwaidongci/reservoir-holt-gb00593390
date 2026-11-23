"""巡查路线重排核心算法。

口径（对应需求）：
- 按各池「最新预报的首次触线日」排巡查，触线日越早越优先。
- 录入路段用时、各站停留和班次起止，从值班室出发并按时返回。
- 目标一：让触线前巡完的池最多；目标二：总用时最短；再以字典序固定唯一结果。
- 封路路段不可通行；重排时已巡路线固定，只从最后签到地点接续。

为便于测试和保证确定性，算法内部统一用「分钟整数」表示时间，
由服务层负责与 datetime 互转。
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

OFFICE = "office"  # 值班室节点编号
_LIMIT = 12  # 池数超过该值时退化为贪心，避免阶乘爆炸


class PatrolError(ValueError):
    """排班输入或签到不满足业务口径。"""


@dataclass(frozen=True)
class Pool:
    code: str
    name: str
    trigger_date: date | None  # 最新预报首次触线日，None 表示窗口内不触线
    dwell_min: int


@dataclass
class Network:
    travel: dict[tuple[str, str], int] = field(default_factory=dict)
    blocked: set[tuple[str, str]] = field(default_factory=set)

    def edge_minutes(self, a, b):
        if a == b:
            return 0
        key = (a, b) if (a, b) in self.travel else (b, a)
        if key in self.blocked:
            return None
        if key not in self.travel:
            return None
        return self.travel[key]

    def reachable(self, a, b):
        return self.edge_minutes(a, b) is not None


def _on_time(visit_minute: int, trigger: date | None, shift_date: date) -> bool:
    if trigger is None:
        return True
    # 触线日当天 0 点为界：班次日期早于触线日即触线前完成。
    return shift_date < trigger


def _score_route(order, start, start_minute, shift_end, pools, net, shift_date):
    """计算一条候选路线的指标；不可行返回 None。

    返回 (按时完成池数, 总用时, 逐站明细)。总用时含回值班室路程。
    """
    by_code = {pool.code: pool for pool in pools}
    clock = start_minute
    current = start
    on_time = 0
    visited = set()
    legs = []
    for code in order:
        ride = net.edge_minutes(current, code)
        if ride is None:
            return None
        arrive = clock + ride
        if arrive > shift_end:
            return None
        pool = by_code[code]
        depart = arrive + pool.dwell_min
        if depart > shift_end:
            return None
        if _on_time(depart, pool.trigger_date, shift_date):
            on_time += 1
        legs.append({"code": code, "ride_min": ride, "arrive_min": arrive,
                     "dwell_min": pool.dwell_min, "depart_min": depart,
                     "on_time": _on_time(depart, pool.trigger_date, shift_date)})
        clock, current = depart, code
        visited.add(code)
    home = net.edge_minutes(current, OFFICE)
    if home is None:
        return None
    back_at = clock + home
    if back_at > shift_end:
        return None
    legs.append({"code": OFFICE, "ride_min": home, "arrive_min": back_at,
                 "dwell_min": 0, "depart_min": back_at, "on_time": True})
    total = back_at - start_minute
    return on_time, total, legs, back_at


def _greedy_order(pools, start, net):
    """池数过多时的兜底：按触线日就近贪心，仅保证给出可行路线。"""
    remaining = [pool.code for pool in pools]
    order, current = [], start
    while remaining:
        candidates = [code for code in remaining if net.reachable(current, code)]
        if not candidates:
            break
        code = min(candidates, key=lambda c: next(p for p in pools if p.code == c).trigger_date or date.max)
        order.append(code)
        remaining.remove(code)
        current = code
    return order


def plan_route(pools, net, shift_start, shift_end, shift_date,
               start=OFFICE, start_minute=None, fixed_prefix=None, exclude_codes=None):
    """枚举可行顺序，按 (触线前巡完池数↓, 覆盖池数↓, 总用时↓, 池编号字典序) 选最优。

    fixed_prefix: 重排时已巡完、必须固定在前的池 code 列表。
    exclude_codes: 重排时人在现场但尚未离开的池，不重复排入待巡序列。
    start/start_minute: 接续地点与接续时刻（分钟）。
    """
    if start_minute is None:
        start_minute = shift_start
    fixed = list(fixed_prefix or [])
    excluded = set(exclude_codes or ())
    fixed_set = set(fixed) | excluded
    pending = [pool for pool in pools if pool.code not in fixed_set]

    # 固定前缀本身必须可通行，且逐站不超时。
    prefix_legs, clock, current = [], start_minute, start
    by_code = {pool.code: pool for pool in pools}
    for code in fixed:
        ride = net.edge_minutes(current, code)
        if ride is None:
            raise PatrolError(f"已巡路线经过封路或缺失路段：{current} → {code}，无法固定历史路线。")
        arrive = clock + ride
        pool = by_code[code]
        depart = arrive + pool.dwell_min
        if depart > shift_end:
            raise PatrolError("固定的已巡路线已超出班次结束时间，请核对签到记录。")
        prefix_legs.append({"code": code, "ride_min": ride, "arrive_min": arrive,
                            "dwell_min": pool.dwell_min, "depart_min": depart,
                            "on_time": _on_time(depart, pool.trigger_date, shift_date)})
        clock, current = depart, code

    best = None  # (按时池数, 覆盖池数, 总用时, 完整顺序, 逐站明细, 返岗分钟)
    codes = [pool.code for pool in pending]

    def consider(order):
        nonlocal best
        result = _score_route(order, current, clock, shift_end, pending, net, shift_date)
        if result is None:
            return
        on_time, total, legs, back_at = result
        # 固定前缀里按时的池数与覆盖数恒等，加回保证全路线口径一致。
        fixed_on_time = sum(1 for leg in prefix_legs if leg["on_time"])
        full_order = tuple(fixed + order)
        fixed_coverage = len(prefix_legs)
        # 目标顺序：触线前巡完池数 ↓ → 覆盖池数 ↓ → 总用时 ↓ → 字典序。
        # 覆盖目标保证「来不及但仍要巡」的池不会被单纯的短路目标丢弃。
        candidate = (on_time + fixed_on_time, len(order) + fixed_coverage,
                     total, full_order, prefix_legs + legs, back_at)
        if best is None:
            best = candidate
            return
        better = (candidate[0], candidate[1], -candidate[2]) > (best[0], best[1], -best[2])
        tied = candidate[:3] == best[:3]
        if better or (tied and candidate[3] < best[3]):
            best = candidate

    if len(codes) > _LIMIT:
        order = _greedy_order(pending, current, net)
        for cut in range(1, len(order) + 1):
            consider(order[:cut])
    else:
        # 带剪枝的深度优先排列枚举；每个非空前缀都参与评估，
        # 这样「舍弃无法按时的池、保留可行子集」的路线也会被比较。
        ordered = sorted(codes, key=lambda c: (by_code[c].trigger_date or date.max, c))

        def dfs(remaining, chosen, node, tick):
            if not remaining:
                return
            for code in tuple(remaining):
                ride = net.edge_minutes(node, code)
                if ride is None:
                    continue
                arrive = tick + ride
                depart = arrive + by_code[code].dwell_min
                if depart > shift_end:
                    continue
                chosen.append(code)
                consider(list(chosen))  # 当前非空前缀本身就是一条可行路线
                dfs(remaining - {code}, chosen, code, depart)
                chosen.pop()

        dfs(set(ordered), [], current, clock)

    if best is None:
        # 没有任何可纳入路线的待巡池：只有当待巡池本身为空时，空路线才合法；
        # 否则说明封路/班次时间导致无解，交由上层提示，而不是悄悄漏巡。
        ride_home = net.edge_minutes(current, OFFICE)
        if ride_home is None or clock + ride_home > shift_end:
            raise PatrolError("当前封路或班次时间内无法返回值班室，请调整路段或班次。")
        if pending:
            raise PatrolError("待巡池在当前封路和班次时间内均无法纳入路线，请调整路段、停留或班次。")
        legs = prefix_legs + [{"code": OFFICE, "ride_min": ride_home, "arrive_min": clock + ride_home,
                               "dwell_min": 0, "depart_min": clock + ride_home, "on_time": True}]
        fixed_on_time = sum(1 for leg in prefix_legs if leg["on_time"])
        return {"order": fixed, "legs": legs, "on_time_count": fixed_on_time,
                "coverage_count": len(prefix_legs),
                "total_min": clock + ride_home - start_minute, "back_min": clock + ride_home,
                "feasible": True, "skipped": [], "in_progress": sorted(excluded)}

    on_time, coverage, total, order, legs, back_at = best
    skipped = [pool.code for pool in pending if pool.code not in order]
    return {"order": list(order), "legs": legs, "on_time_count": on_time,
            "coverage_count": coverage,
            "total_min": total, "back_min": back_at,
            "feasible": not skipped, "skipped": skipped,
            "in_progress": sorted(excluded)}


# ---- 时间换算辅助（服务层共用） ----

def minutes_of(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def shift_bounds(shift_date: date, start_clock: time, end_clock: time) -> tuple[int, int]:
    start = start_clock.hour * 60 + start_clock.minute
    end = end_clock.hour * 60 + end_clock.minute
    if end <= start:
        raise PatrolError("班次结束时间必须晚于开始时间。")
    return start, end


def at_minute(shift_date: date, minute: int) -> datetime:
    return datetime.combine(shift_date, time(0, 0)) + timedelta(minutes=minute)
