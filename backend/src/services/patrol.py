"""巡查排班服务：路线生成、原子切换、重排固定、签到只追加。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.domain.patrol import OFFICE, Network, PatrolError, Pool, minutes_of, plan_route
from src.models.patrol import (PatrolCheckin, PatrolEdge, PatrolPlan, PatrolPool, PatrolShift)
from src.utils.time import now, to_local

PLANNING = 'planning'
ACTIVE = 'active'
PENDING_REPLAN = 'pending_replan'
ARCHIVED = 'archived'


# ---- 路网 / 池快照 ----

def _network(db) -> Network:
    network = Network()
    for edge in db.scalars(select(PatrolEdge)):
        key = tuple(sorted((edge.node_a, edge.node_b)))
        network.travel[key] = edge.travel_min
        if edge.blocked:
            network.blocked.add(key)
    return network


def _pools(db) -> list[PatrolPool]:
    return list(db.scalars(select(PatrolPool).where(PatrolPool.enabled.is_(True)).order_by(PatrolPool.code)))


def _pool_domains(rows: list[PatrolPool]) -> list[Pool]:
    return [Pool(code=row.code, name=row.name, trigger_date=row.trigger_date, dwell_min=row.dwell_min)
            for row in rows]


def _edge_snapshot(network: Network) -> dict:
    return {'travel': [{'nodes': list(key), 'minutes': minutes} for key, minutes in sorted(network.travel.items())],
            'blocked': [list(key) for key in sorted(network.blocked)]}


def _trigger_snapshot(pools: list[PatrolPool]) -> dict:
    return {row.code: row.trigger_date.isoformat() if row.trigger_date else None for row in pools}


# ---- 路线生成与版本切换 ----

def _shift_or_404(db, shift_id, lock=False) -> PatrolShift:
    shift = db.get(PatrolShift, shift_id, with_for_update=lock)
    if not shift:
        raise LookupError('找不到该巡查班次')
    return shift


def _active_plan(db, shift_id):
    return db.scalar(select(PatrolPlan).where(PatrolPlan.shift_id == shift_id,
                                              PatrolPlan.state == ACTIVE).with_for_update())


def build_plan(db, shift: PatrolShift, reason, author, *, fixed_codes=None, start_node=OFFICE,
               start_minute=None, exclude_codes=None):
    """算齐一条新路线。调用方负责事务与版本状态切换。"""
    pools = _pools(db)
    if not pools:
        raise PatrolError('还没有启用的巡查池，请先维护池位台账。')
    network = _network(db)
    shift_start, shift_end = _shift_minutes(shift)
    result = plan_route(_pool_domains(pools), network, shift_start, shift_end, shift.shift_date,
                        start=start_node, start_minute=start_minute, fixed_prefix=fixed_codes,
                        exclude_codes=exclude_codes)
    return result, network, pools


def _shift_minutes(shift: PatrolShift):
    start = shift.start_clock.hour * 60 + shift.start_clock.minute
    end = shift.end_clock.hour * 60 + shift.end_clock.minute
    if end <= start:
        raise PatrolError('班次结束时间必须晚于开始时间。')
    return start, end


def create_initial_plan(db, shift: PatrolShift, author):
    """新班次的首版路线：算齐后一次性置为 active。"""
    existing = db.scalar(select(PatrolPlan).where(PatrolPlan.shift_id == shift.id))
    if existing:
        raise PatrolError('该班次已有路线，重排请使用封路/预报更新入口。')
    result, network, pools = build_plan(db, shift, '开班排班', author)
    plan = PatrolPlan(shift_id=shift.id, version=1, state=PLANNING, route=result,
                      trigger_snapshot=_trigger_snapshot(pools), edge_snapshot=_edge_snapshot(network),
                      on_time_count=result['on_time_count'], total_min=result['total_min'],
                      trigger_reason='开班排班', author=author, activated_at=now())
    db.add(plan)
    db.flush()
    plan.state = ACTIVE  # 算齐后一次性切换
    return plan


def replan(db, shift_id: int, reason: str, author: str):
    """封路或预报更新触发的重排。

    固定已巡路线，从最后签到地点/时间接续；旧 active 标 pending_replan，
    新版本算齐后一次性切换为 active。计算失败时旧路线保持可执行。
    """
    shift = _shift_or_404(db, shift_id, lock=True)
    current = _active_plan(db, shift_id)
    if not current:
        raise PatrolError('该班次还没有可执行路线，无法重排。')

    checkins = list(db.scalars(select(PatrolCheckin).where(PatrolCheckin.shift_id == shift_id)
                               .order_by(PatrolCheckin.seq).with_for_update()))
    fixed_codes, exclude_codes, start_node, start_minute = _resume_point(shift, checkins)

    # 等候重排期间，旧路线先标记为待重排（仍可签到，保证现场不中断）。
    current.state = PENDING_REPLAN
    db.flush()

    try:
        result, network, pools = build_plan(db, shift, reason, author, fixed_codes=fixed_codes,
                                            start_node=start_node, start_minute=start_minute,
                                            exclude_codes=exclude_codes)
    except PatrolError:
        # 新路线算不齐：恢复旧路线为 active，让现场继续按旧路线巡查。
        current.state = ACTIVE
        raise

    next_version = db.scalar(select(PatrolPlan.version).where(PatrolPlan.shift_id == shift_id)
                             .order_by(PatrolPlan.version.desc()).limit(1)) + 1
    plan = PatrolPlan(shift_id=shift_id, version=next_version, state=PLANNING, route=result,
                      trigger_snapshot=_trigger_snapshot(pools), edge_snapshot=_edge_snapshot(network),
                      on_time_count=result['on_time_count'], total_min=result['total_min'],
                      trigger_reason=reason, author=author)
    db.add(plan)
    db.flush()
    # 新路线算齐，一次性切换。
    plan.state = ACTIVE
    plan.activated_at = now()
    current.state = ARCHIVED
    return plan, current


def _resume_point(shift: PatrolShift, checkins: list[PatrolCheckin]):
    """从签到流水推导接续地点/时刻、已巡固定池序与在场未离开的池。"""
    if not checkins:
        return [], [], OFFICE, None
    arrived, departed = set(), []
    for item in checkins:
        if item.kind == 'arrive' and item.pool_code:
            arrived.add(item.pool_code)
        elif item.kind == 'depart' and item.pool_code:
            departed.append(item.pool_code)
    last = checkins[-1]
    start_node = last.pool_code or OFFICE
    local = to_local(last.event_at)
    if local.date() != shift.shift_date:
        raise PatrolError('最后签到不在本班次日期内，无法据此接续重排。')
    start_minute = minutes_of(local)
    fixed = list(dict.fromkeys(departed))
    # 最后到达但尚未离开的池：人在现场，固定其位置，不重复排入待巡序列。
    in_progress = sorted(code for code in arrived
                         if code not in set(fixed) and code == start_node)
    return fixed, in_progress, start_node, start_minute


# ---- 签到（两班并发、只追加、旧计算不覆盖新签到） ----

_CHECKIN_RETRIES = 3


def add_checkin(db, shift_id, pool_code, kind, event_at: datetime, actor, note):
    shift = _shift_or_404(db, shift_id, lock=True)
    plan = _active_plan(db, shift_id)
    if not plan:
        raise PatrolError('该班次没有执行中的路线，无法签到。')
    if pool_code is None and kind not in ('return', 'depart'):
        raise PatrolError('值班室只登记出发（depart）或返回（return）。')
    if pool_code is not None and kind == 'return':
        raise PatrolError('返回值班室签到请留空池号。')

    # 班次行锁先把并发签到串行化；唯一约束 (shift_id, seq) 再兜底。
    # 撞号时在保存点内重试取号，保证两班都不漏记，且晚算完的旧任务不会覆盖新签到。
    for attempt in range(_CHECKIN_RETRIES):
        last_seq = db.scalar(select(PatrolCheckin.seq).where(PatrolCheckin.shift_id == shift_id)
                             .order_by(PatrolCheckin.seq.desc()).limit(1).with_for_update())
        seq = (last_seq or 0) + 1
        item = PatrolCheckin(shift_id=shift_id, plan_id=plan.id, seq=seq, pool_code=pool_code,
                             kind=kind, event_at=event_at.replace(tzinfo=None), actor=actor, note=note)
        db.add(item)
        try:
            savepoint = db.begin_nested()
            db.flush()
            savepoint.commit()
            return item
        except IntegrityError:
            savepoint.rollback()
            if attempt == _CHECKIN_RETRIES - 1:
                raise PatrolError('签到冲突重试多次仍失败，请刷新后重试。') from None
    raise PatrolError('签到冲突，请刷新后重试。')
