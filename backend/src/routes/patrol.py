"""巡查排班接口。

- 池位与路段台账（录入停留、路段用时、封路）。
- 班次与路线：开班排班、封路/预报更新重排（固定已巡路线、原子切换）。
- 签到：两班并发只追加，旧任务不能覆盖新签到。
- 交班：到离时间、能否触线前巡完、赶不及的池。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.domain.patrol import OFFICE, PatrolError
from src.models.identity import User
from src.models.jobs import Run
from src.models.patrol import (PatrolCheckin, PatrolEdge, PatrolPlan, PatrolPool, PatrolShift)
from src.schemas.requests import CheckinRequest, EdgeUpsert, PoolUpsert, ReplanRequest, ShiftRequest
from src.services import patrol as service
from src.services.audit import record
from src.utils import views
from src.utils.time import now, to_local, to_utc_naive

router = APIRouter(prefix='/patrol')
_WRITERS = (Role.ADMIN, Role.ANALYST)


def _as_http(error: LookupError | PatrolError):
    return HTTPException(404 if isinstance(error, LookupError) else 409, str(error))


# ---- 池位台账（触线日来自最新预报） ----

@router.get('/pools')
def list_pools(db: Session = Depends(connection), actor: User = Depends(current)):
    rows = db.scalars(select(PatrolPool).order_by(PatrolPool.code)).all()
    return [views.pool_view(item) for item in rows]


@router.post('/pools', status_code=201)
def upsert_pool(body: PoolUpsert, db: Session = Depends(connection),
                actor: User = Depends(allow(*_WRITERS))):
    if body.source_run_id and not db.get(Run, body.source_run_id):
        raise HTTPException(404, '找不到关联的预报运行记录')
    item = db.scalar(select(PatrolPool).where(PatrolPool.code == body.code))
    if item:
        item.name, item.trigger_date, item.source_run_id = body.name, body.trigger_date, body.source_run_id
        item.dwell_min, item.enabled, item.updated_at = body.dwell_min, body.enabled, now()
        action = 'patrol.pool_updated'
    else:
        item = PatrolPool(**body.model_dump(), updated_at=now())
        db.add(item)
        action = 'patrol.pool_created'
    db.flush()
    record(db, actor.username, action, item.code,
           trigger_date=body.trigger_date.isoformat() if body.trigger_date else None)
    db.commit()
    return views.pool_view(item)


# ---- 路段（含封路） ----

@router.get('/edges')
def list_edges(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.edge_view(item) for item in db.scalars(select(PatrolEdge).order_by(PatrolEdge.id))]


@router.post('/edges', status_code=201)
def upsert_edge(body: EdgeUpsert, db: Session = Depends(connection),
                actor: User = Depends(allow(*_WRITERS))):
    if body.node_a == body.node_b:
        raise HTTPException(422, '路段两端不能是同一地点')
    key = tuple(sorted((body.node_a, body.node_b)))
    item = db.scalar(select(PatrolEdge).where(PatrolEdge.node_a == key[0], PatrolEdge.node_b == key[1]))
    if item:
        item.travel_min, item.blocked, item.note, item.updated_at = body.travel_min, body.blocked, body.note, now()
        action = 'patrol.edge_updated'
    else:
        item = PatrolEdge(node_a=key[0], node_b=key[1], travel_min=body.travel_min,
                          blocked=body.blocked, note=body.note, updated_at=now())
        db.add(item)
        action = 'patrol.edge_created'
    db.flush()
    record(db, actor.username, action, f'{key[0]}-{key[1]}', blocked=body.blocked)
    db.commit()
    return views.edge_view(item)


# ---- 班次 ----

@router.get('/shifts')
def list_shifts(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.shift_view(item)
            for item in db.scalars(select(PatrolShift).order_by(PatrolShift.shift_date.desc(), PatrolShift.id.desc()))]


@router.post('/shifts', status_code=201)
def create_shift(body: ShiftRequest, db: Session = Depends(connection),
                 actor: User = Depends(allow(*_WRITERS))):
    if body.end_clock <= body.start_clock:
        raise HTTPException(422, '班次结束时间必须晚于开始时间')
    item = PatrolShift(**body.model_dump())
    db.add(item)
    db.flush()
    record(db, actor.username, 'patrol.shift_created', item.id, team=item.team)
    db.commit()
    return views.shift_view(item)


# ---- 路线：开班排班 / 重排 / 查询 ----

@router.get('/shifts/{shift_id}/plans')
def list_plans(shift_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    if not db.get(PatrolShift, shift_id):
        raise HTTPException(404, '找不到该巡查班次')
    rows = db.scalars(select(PatrolPlan).where(PatrolPlan.shift_id == shift_id).order_by(PatrolPlan.version)).all()
    return [views.plan_view(item) for item in rows]


@router.post('/shifts/{shift_id}/plan', status_code=201)
def initial_plan(shift_id: int, db: Session = Depends(connection),
                 actor: User = Depends(allow(*_WRITERS))):
    shift = db.get(PatrolShift, shift_id, with_for_update=True)
    if not shift:
        raise HTTPException(404, '找不到该巡查班次')
    try:
        plan = service.create_initial_plan(db, shift, actor.username)
        record(db, actor.username, 'patrol.plan_activated', plan.id, version=plan.version,
               on_time=plan.on_time_count, total_min=plan.total_min)
        db.commit()
    except PatrolError as error:
        db.rollback()
        raise _as_http(error)
    return views.plan_view(plan)


@router.post('/replan', status_code=201)
def replan(body: ReplanRequest, db: Session = Depends(connection),
           actor: User = Depends(allow(*_WRITERS))):
    try:
        plan, previous = service.replan(db, body.shift_id, body.reason, actor.username)
        record(db, actor.username, 'patrol.replan_activated', plan.id, version=plan.version,
               reason=body.reason, replaced=previous.id, on_time=plan.on_time_count,
               total_min=plan.total_min)
        db.commit()
    except (LookupError, PatrolError) as error:
        db.rollback()
        raise _as_http(error)
    return views.plan_view(plan)


# ---- 签到 ----

@router.get('/shifts/{shift_id}/checkins')
def list_checkins(shift_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    if not db.get(PatrolShift, shift_id):
        raise HTTPException(404, '找不到该巡查班次')
    rows = db.scalars(select(PatrolCheckin).where(PatrolCheckin.shift_id == shift_id)
                      .order_by(PatrolCheckin.seq)).all()
    return [views.checkin_view(item) for item in rows]


@router.post('/checkins', status_code=201)
def checkin(body: CheckinRequest, db: Session = Depends(connection),
            actor: User = Depends(allow(*_WRITERS))):
    try:
        item = service.add_checkin(db, body.shift_id, body.pool_code, body.kind,
                                   to_utc_naive(body.event_at), actor.username, body.note)
        record(db, actor.username, 'patrol.checkin', item.id, seq=item.seq,
               pool=item.pool_code or OFFICE, kind=item.kind)
        db.commit()
    except (LookupError, PatrolError) as error:
        db.rollback()
        raise _as_http(error)
    return views.checkin_view(item)


# ---- 交班报告 ----

@router.get('/shifts/{shift_id}/handover')
def handover(shift_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    shift = db.get(PatrolShift, shift_id)
    if not shift:
        raise HTTPException(404, '找不到该巡查班次')
    plan = db.scalar(select(PatrolPlan).where(PatrolPlan.shift_id == shift_id,
                                              PatrolPlan.state == service.ACTIVE))
    checkins = db.scalars(select(PatrolCheckin).where(PatrolCheckin.shift_id == shift_id)
                          .order_by(PatrolCheckin.seq)).all()
    pools = {item.code: item for item in db.scalars(select(PatrolPool))}

    visits = _visit_timeline(shift, checkins)
    missed = _missed_pools(shift, plan, pools, visits)
    # 值班室的第一次离开即出发；最后一次回到值班室即返岗。
    office_events = [item for item in checkins if not item.pool_code]
    depart_event = next((item for item in office_events if item.kind == 'depart'), None)
    return_event = next((item for item in reversed(office_events) if item.kind == 'return'), None)
    return {
        'shift': views.shift_view(shift),
        'plan': views.plan_view(plan) if plan else None,
        'depart_at': to_local(depart_event.event_at).isoformat(timespec='seconds') if depart_event else None,
        'return_at': to_local(return_event.event_at).isoformat(timespec='seconds') if return_event else None,
        'visits': visits,
        'in_progress': plan.route.get('in_progress', []) if plan else [],
        'missed_pools': missed,
        'checkin_count': len(checkins),
        'generated_at': views.iso(now()),
    }


def _visit_timeline(shift, checkins):
    """把到达/离开流水合并成每个地点的到离时间；值班室按出发/返回单列。"""
    timeline = []
    pending = {}
    for item in checkins:
        location = item.pool_code or OFFICE
        stamp = to_local(item.event_at).isoformat(timespec='seconds')
        if item.kind == 'arrive':
            pending[location] = stamp
        elif item.kind == 'depart':
            if location == OFFICE:
                timeline.append({'location': OFFICE, 'arrive': None, 'depart': stamp, 'leg': '出发'})
            else:
                timeline.append({'location': location, 'arrive': pending.pop(location, None),
                                 'depart': stamp, 'leg': '巡查'})
        elif item.kind == 'return':
            timeline.append({'location': OFFICE, 'arrive': stamp, 'depart': None, 'leg': '返回'})
    for location, arrive in pending.items():
        timeline.append({'location': location, 'arrive': arrive, 'depart': None, 'leg': '巡查'})
    return timeline


def _missed_pools(shift, plan, pools, visits):
    """赶不及 = 路线要求巡、但无签到，或签到离开时间不早于触线日。"""
    visited_locations = {row['location'] for row in visits}
    visited_depart = {row['location']: row['depart'] for row in visits if row['depart']}
    ordered = plan.route['order'] if plan else []
    skipped = plan.route.get('skipped', []) if plan else []
    missed, seen = [], set()

    def add_missed(code, reason):
        if code in seen:
            return
        seen.add(code)
        pool = pools.get(code)
        missed.append({'code': code, 'name': pool.name if pool else code,
                       'trigger_date': pool.trigger_date.isoformat() if pool and pool.trigger_date else None,
                       'reason': reason})

    for code in ordered:
        pool = pools.get(code)
        trigger = pool.trigger_date if pool else None
        before_trigger = True
        if trigger and code in visited_depart:
            before_trigger = visited_depart[code][:10] < trigger.isoformat()
        if code not in visited_locations:
            add_missed(code, '未签到')
        elif not before_trigger:
            add_missed(code, '离开时间晚于或等于触线日')
    # 路线本身就跳过的池（封路/班次时间排不进）。
    for code in skipped:
        add_missed(code, '班次时间/封路导致未能排入路线')
    return missed
