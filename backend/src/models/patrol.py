"""巡查排班相关表结构。

- PatrolPool：巡查池及其最新预报触线日（随预报复核更新）。
- PatrolEdge：路段用时；blocked=True 表示封路。
- PatrolShift：班次起止与值班室约束。
- PatrolPlan：路线版本。active 版本唯一；重排时旧版本置 pending_replan，新版本算齐后一次性切换。
- PatrolCheckin：两班共用的只追加签到流水，序号单调，迟到的旧计算不能覆盖新签到。
"""

from datetime import date, datetime, time

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.utils.time import now


class PatrolPool(Base):
    __tablename__ = 'patrol_pools'
    __table_args__ = (UniqueConstraint('code'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    # 最新预报的首次触线日；窗口内不触线时为空。
    trigger_date: Mapped[date | None] = mapped_column(Date, index=True)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey('runs.id'))
    dwell_min: Mapped[int] = mapped_column(Integer, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class PatrolEdge(Base):
    __tablename__ = 'patrol_edges'
    __table_args__ = (UniqueConstraint('node_a', 'node_b'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    node_a: Mapped[str] = mapped_column(String(40), index=True)
    node_b: Mapped[str] = mapped_column(String(40), index=True)
    travel_min: Mapped[int] = mapped_column(Integer)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    note: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(default=now)


class PatrolShift(Base):
    __tablename__ = 'patrol_shifts'

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_date: Mapped[date] = mapped_column(Date, index=True)
    team: Mapped[str] = mapped_column(String(64))
    start_clock: Mapped[time] = mapped_column(Time)
    end_clock: Mapped[time] = mapped_column(Time)
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=now)


class PatrolPlan(Base):
    __tablename__ = 'patrol_plans'
    __table_args__ = (UniqueConstraint('shift_id', 'version'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey('patrol_shifts.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # planning=计算中（旧 active 保持执行）；active=当前执行；pending_replan=等候新路线；archived=被替换。
    state: Mapped[str] = mapped_column(String(20), default='planning', index=True)
    route: Mapped[list] = mapped_column(JSON)          # plan_route 完整结果
    trigger_snapshot: Mapped[dict] = mapped_column(JSON)  # 生成时各池触线日快照
    edge_snapshot: Mapped[dict] = mapped_column(JSON)     # 生成时路网快照（含封路）
    on_time_count: Mapped[int] = mapped_column(Integer, default=0)
    total_min: Mapped[int] = mapped_column(Integer, default=0)
    trigger_reason: Mapped[str | None] = mapped_column(String(200))  # 封路/预报更新
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    activated_at: Mapped[datetime | None]


class PatrolCheckin(Base):
    __tablename__ = 'patrol_checkins'
    __table_args__ = (UniqueConstraint('shift_id', 'seq'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey('patrol_shifts.id'), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('patrol_plans.id'))
    seq: Mapped[int] = mapped_column(Integer)          # 班次内单调序号，只追加
    pool_code: Mapped[str | None] = mapped_column(String(40))  # None=值班室
    kind: Mapped[str] = mapped_column(String(20))      # arrive / depart / return
    event_at: Mapped[datetime] = mapped_column(index=True)
    actor: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=now)
