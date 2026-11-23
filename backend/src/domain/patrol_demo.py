"""巡查排班演示台账：多座雨水回用池、园区路段与一个白班。

触线日与既有 RW-02 预报口径呼应：RW-02 预计 11 月 23 日首次触线。
"""

from datetime import date, time

from sqlalchemy import select

from src.domain.patrol import OFFICE
from src.models.patrol import PatrolEdge, PatrolPool, PatrolShift

# code, 名称, 最新预报首次触线日, 站内停留（分钟）
DEMO_POOLS = [
    ('RW-02', '会展园主回用池', date(2026, 11, 23), 15),
    ('RW-05', '西区雨水调蓄池', date(2026, 11, 24), 10),
    ('RW-08', '东广场回用池', date(2026, 11, 25), 10),
    ('RW-11', '地下车库集水池', None, 8),  # 预报窗口内不触线
]

# 节点 A, 节点 B, 用时（分钟）, 是否封路
DEMO_EDGES = [
    (OFFICE, 'RW-02', 12, False),
    (OFFICE, 'RW-05', 25, False),
    (OFFICE, 'RW-08', 35, False),
    (OFFICE, 'RW-11', 18, False),
    ('RW-02', 'RW-05', 14, False),
    ('RW-05', 'RW-08', 16, False),
    ('RW-02', 'RW-11', 20, False),
    ('RW-08', 'RW-11', 30, False),
]

DEMO_SHIFT = (date(2026, 11, 23), '白班', time(8, 0), time(12, 0), '演示班次：从值班室出发并按时返回')


def seed_patrol(db):
    """幂等写入巡查演示台账，返回写入条数。"""
    created = {'pools': 0, 'edges': 0, 'shifts': 0}
    for code, name, trigger, dwell in DEMO_POOLS:
        if _pool_absent(db, code):
            db.add(PatrolPool(code=code, name=name, trigger_date=trigger, dwell_min=dwell))
            created['pools'] += 1
    for a, b, minutes, blocked in DEMO_EDGES:
        node_a, node_b = sorted((a, b))
        exists = db.scalar(select(PatrolEdge).where(PatrolEdge.node_a == node_a,
                                                     PatrolEdge.node_b == node_b)) is not None
        if not exists:
            db.add(PatrolEdge(node_a=node_a, node_b=node_b, travel_min=minutes, blocked=blocked))
            created['edges'] += 1
    shift_date, team, start, end, note = DEMO_SHIFT
    has_shift = db.scalar(select(PatrolShift).where(PatrolShift.shift_date == shift_date,
                                                     PatrolShift.team == team)) is not None
    if not has_shift:
        db.add(PatrolShift(shift_date=shift_date, team=team, start_clock=start, end_clock=end, note=note))
        created['shifts'] += 1
    return created


def _pool_absent(db, code):
    from sqlalchemy import select
    return db.scalar(select(PatrolPool).where(PatrolPool.code == code)) is None
