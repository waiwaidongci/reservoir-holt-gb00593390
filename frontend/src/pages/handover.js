import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {workspace} from '../stores/workspace.js';
import {el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const KIND_LABEL = {arrive: '到达', depart: '离开', return: '返回'};

export async function handoverPage(route) {
  const shiftId = route.id;
  if (!shiftId) {
    return panel('巡查交班', empty('请先在排班台选择一个班次。', '#patrol', '前往排班台'));
  }
  const report = await workspace.handover(shiftId);
  const {shift, plan} = report;
  const summary = metrics([
    {label: '班次', value: `${shift.shift_date} ${shift.team}`},
    {label: '值班室出发', value: report.depart_at ? date(report.depart_at) : '未登记'},
    {label: '返回值班室', value: report.return_at ? date(report.return_at) : '未返回'},
    {label: '签到条数', value: report.checkin_count},
    {label: '执行版本', value: plan ? `v${plan.version}` : '无'},
    {label: '赶不及的池', value: report.missed_pools.length,
     detail: report.missed_pools.length ? '见下方明细' : '全部触线前巡完'},
    ...(report.in_progress?.length ? [{label: '在场未离开', value: report.in_progress.join('、'),
      detail: '重排时作为接续点，未重复排入路线'}] : []),
  ]);

  const visits = table([
    {key: 'seq', label: '序号', render: (row, index) => index + 1},
    {label: '地点', render: row => row.location === 'office' ? '值班室' : row.location},
    {label: '环节', render: row => row.leg || ''},
    {label: '到达时间', render: row => row.arrive ? date(row.arrive) : '—'},
    {label: '离开时间', render: row => row.depart ? date(row.depart) : '—'},
  ], report.visits, {caption: '各站到离时间', empty: '本班次还没有签到。'});

  const missed = table([
    {key: 'code', label: '池编号'}, {key: 'name', label: '名称'},
    {label: '触线日', render: row => row.trigger_date || '窗口内不触线'},
    {key: 'reason', label: '赶不及原因'},
  ], report.missed_pools, {caption: '赶不及的池（未触线前巡完）', empty: '没有赶不及的池，全部按口径巡完。'});

  const planNote = plan ? panel('执行路线快照',
    p(`v${plan.version} · 触线前巡完 ${plan.on_time_count} 池 · 总用时 ${plan.total_min} 分钟 · 触发：${plan.trigger_reason || '—'}`, 'subtle'),
    pill(plan.state, plan.state),
    table([
      {label: '顺序', render: (row, index) => row.code === 'office' ? '↩ 值班室' : `${index + 1}. ${row.code}`},
      {key: 'ride_min', label: '路段(分)'}, {key: 'arrive_min', label: '到达(分)'},
      {key: 'dwell_min', label: '停留(分)'}, {key: 'depart_min', label: '离开(分)'},
      {label: '触线前', render: row => row.code === 'office' ? '—' : (row.on_time ? '是' : '否')},
    ], plan.route.legs, {caption: `v${plan.version} 逐站时刻`}))
    : panel('执行路线快照', empty('该班次没有执行路线。', '#patrol', '前往排班台'));

  return el('div', {},
    el('div', {class: 'actions'}, link('← 返回排班台', `#patrol/${shiftId}`, {class: 'quiet'})),
    panel(`${shift.shift_date} ${shift.team} · 巡查交班报告`,
      p('列出值班室到离时间、各站到离时刻，以及未能在触线前巡完的池。', 'subtle'), summary),
    panel('到离时间', visits),
    panel('赶不及的池', report.missed_pools.length
      ? el('div', {}, p(`共 ${report.missed_pools.length} 个池未在触线前巡完：`, 'feedback'), missed)
      : missed),
    planNote);
}
