# 002 巡查排班表结构

应用启动时 `Base.metadata.create_all` 只补建缺失表，本迁移为新增表，不改动既有表，可安全随启动自动创建。

- `patrol_pools` 巡查池台账：编号唯一，保存最新预报首次触线日、来源运行、站内停留分钟与启用状态。
- `patrol_edges` 路段：无向边按节点字母序存储 `(node_a, node_b)` 唯一约束，保存用时与封路标记。
- `patrol_shifts` 班次：班次日期、班组、起止钟点。
- `patrol_plans` 路线版本：班次内版本号唯一，状态在 `planning / active / pending_replan / archived` 间流转，路线、触线快照与路网快照以 JSON 留存。
- `patrol_checkins` 签到流水：只追加，`(shift_id, seq)` 唯一约束保证两班并发不串号，事件时间统一存 naive UTC。

重排与签到的事务口径见 `src/services/patrol.py`；路线寻优见 `src/domain/patrol.py`。
