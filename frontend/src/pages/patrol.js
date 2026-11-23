import {feedback} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {CHECKIN_KIND, PLAN_STATE} from '../constants/enums.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const OFFICE = 'office';

function input(name, label, {type = 'text', required = true, value: val = '', step, min, max} = {}) {
  return el('label', {class: 'field'}, el('span', {}, label),
    el('input', {name, type, required, value: val, step, min, max, autocomplete: 'off'}));
}

function select(name, label, options, required = true) {
  return el('label', {class: 'field'}, el('span', {}, label),
    el('select', {name, required}, options.map(([v, text]) => el('option', {value: v}, text))));
}

// ---- 池位台账 ----

function poolPanel(pools, refresh, message) {
  const form = el('form', {class: 'fields form-grid'},
    input('code', '池编号'), input('name', '池名称'),
    input('trigger_date', '最新触线日', {type: 'date', required: false}),
    input('dwell_min', '停留（分钟）', {type: 'number', step: '1', min: 0, max: 600, value: 10}),
    el('label', {class: 'check'}, el('input', {name: 'enabled', type: 'checkbox', checked: true}), '启用巡查'),
    button('保存池位', {type: 'submit', class: 'primary'}));
  form.addEventListener('submit', handler(form.querySelector('button'), message, async event => {
    const d = new FormData(event.target);
    await workspace.savePool({
      code: d.get('code'), name: d.get('name'),
      trigger_date: d.get('trigger_date') || null, dwell_min: Number(d.get('dwell_min')),
      enabled: d.has('enabled'),
    });
    await refresh();
  }));
  const grid = table([
    {key: 'code', label: '池编号'}, {key: 'name', label: '名称'},
    {label: '触线日', render: row => row.trigger_date || '窗口内不触线'},
    {key: 'dwell_min', label: '停留(分)'},
    {label: '状态', render: row => pill(row.enabled ? '启用' : '停用', row.enabled ? 'active' : 'archived')},
  ], pools, {caption: '巡查池与最新触线日', empty: '先录入巡查池和最新预报触线日。'});
  return panel('巡查池台账（按最新预报触线日排序）',
    p('触线日来自最新一次已复核预报；预报更新后在此改日期并触发重排。', 'subtle'),
    session.canWrite ? form : p('只读角色可查看台账。', 'subtle'), grid);
}

// ---- 路段台账 ----

function edgePanel(edges, refresh, message) {
  const form = el('form', {class: 'fields form-grid'},
    input('node_a', '地点 A', {value: OFFICE}), input('node_b', '地点 B'),
    input('travel_min', '路段用时（分钟）', {type: 'number', step: '1', min: 1, max: 1440}),
    el('label', {class: 'check'}, el('input', {name: 'blocked', type: 'checkbox'}), '封路（不可通行）'),
    button('保存路段', {type: 'submit', class: 'primary'}));
  form.addEventListener('submit', handler(form.querySelector('button'), message, async event => {
    const d = new FormData(event.target);
    const a = d.get('node_a').trim(), b = d.get('node_b').trim();
    if (a === b) throw new Error('路段两端不能相同');
    await workspace.saveEdge({node_a: a, node_b: b, travel_min: Number(d.get('travel_min')),
      blocked: d.has('blocked')});
    await refresh();
  }));
  const grid = table([
    {label: '路段', render: row => `${row.node_a} ⇄ ${row.node_b}`},
    {key: 'travel_min', label: '用时(分)'},
    {label: '状态', render: row => pill(row.blocked ? '封路' : '通行', row.blocked ? 'failed' : 'complete')},
    {key: 'note', label: '备注'},
  ], edges, {caption: '园区路段用时与封路', empty: '先录入值班室到各池的路段用时。'});
  return panel('路段用时与封路',
    p('封路路段不会进入路线；封路后对进行中班次发起重排，固定已巡路线。', 'subtle'),
    session.canWrite ? form : p('只读角色可查看路段。', 'subtle'), grid);
}

// ---- 班次与路线 ----

function shiftPanel(shifts, refresh, message) {
  const form = el('form', {class: 'fields form-grid'},
    input('shift_date', '班次日期', {type: 'date'}), input('team', '班组', {value: '白班'}),
    input('start_clock', '开始时间', {type: 'time', value: '08:00'}),
    input('end_clock', '结束时间', {type: 'time', value: '12:00'}),
    button('开班次', {type: 'submit', class: 'primary'}));
  form.addEventListener('submit', handler(form.querySelector('button'), message, async event => {
    const d = new FormData(event.target);
    await workspace.createShift({
      shift_date: d.get('shift_date'), team: d.get('team'),
      start_clock: d.get('start_clock'), end_clock: d.get('end_clock'),
    });
    await refresh();
  }));
  const grid = table([
    {label: '班次', render: row => `${row.shift_date} ${row.team}`},
    {label: '起止', render: row => `${row.start_clock}–${row.end_clock}`},
    {key: 'note', label: '备注'},
    {label: '排班', render: row => link('打开排班台', `#patrol/${row.id}`)},
  ], shifts, {caption: '班次', empty: '先创建一个班次。'});
  return panel('班次起止（从值班室出发并按时返回）',
    session.canWrite ? form : p('只读角色可查看班次。', 'subtle'), grid);
}

function legLabel(code, pools) {
  if (code === OFFICE) return '值班室';
  return pools.find(pool => pool.code === code)?.name || code;
}

function routePanel(shiftId, plans, pools, refresh, message) {
  const active = plans.find(plan => plan.state === 'active');
  const versions = table([
    {label: '版本', render: row => `v${row.version}`},
    {label: '状态', render: row => pill(PLAN_STATE[row.state], row.state)},
    {key: 'on_time_count', label: '触线前巡完(池)'},
    {key: 'total_min', label: '总用时(分)'},
    {key: 'trigger_reason', label: '触发原因'},
    {label: '时间', render: row => date(row.activated_at || row.created_at)},
  ], [...plans].reverse(), {caption: '路线版本（新路线算齐后一次性切换）', empty: '还没有路线。'});

  const detail = active ? el('div', {},
    metrics([
      {label: '执行版本', value: `v${active.version}`},
      {label: '触线前可巡完', value: `${active.on_time_count} 池`},
      {label: '纳入路线', value: `${active.route.coverage_count ?? active.route.order.length} 池`},
      {label: '路线总用时', value: `${active.total_min} 分钟`},
      {label: '能否全巡', value: active.route.feasible ? '全部可巡' : `跳过 ${active.route.skipped.length} 池`},
      ...(active.route.in_progress?.length ? [{label: '在场未离开', value: active.route.in_progress.join('、')}] : []),
    ]),
    table([
      {label: '顺序', render: (row, idx) => row.code === OFFICE ? '↩ 值班室' : `${idx + 1}. ${legLabel(row.code, pools)}`},
      {key: 'ride_min', label: '路段(分)'},
      {label: '到达(分)', render: row => row.arrive_min},
      {key: 'dwell_min', label: '停留(分)'},
      {label: '离开(分)', render: row => row.depart_min},
      {label: '触线前', render: row => row.code === OFFICE ? '—' : pill(row.on_time ? '是' : '否', row.on_time ? 'complete' : 'failed')},
    ], active.route.legs, {caption: `v${active.version} 逐站时刻（分钟轴，0=午夜）`}))
    : empty('该班次还没有执行路线。', null, null);

  const build = button('开班排班（算齐后一次性切换）', {class: 'primary'});
  build.addEventListener('click', handler(build, message, async () => {
    await workspace.activatePlan(shiftId);
    await refresh();
  }));
  const replanBtn = button('封路/预报更新 · 重排（固定已巡路线）', {class: 'quiet'});
  replanBtn.addEventListener('click', handler(replanBtn, message, async () => {
    const reason = prompt('请输入重排原因（如：office-RW-05 封路 / RW-02 触线日提前）');
    if (!reason) return;
    await workspace.replan({shift_id: shiftId, reason});
    await refresh();
  }));
  return panel('排班台',
    p('先最大化触线前巡完的池数，再最小化总用时；重排从最后签到地点和时间接续，已巡路线固定。', 'subtle'),
    session.canWrite && el('div', {class: 'actions'}, plans.length ? replanBtn : build, message),
    detail, active && p(`旧路线在等候重排期间标记为「待重排」，现场仍可签到；新路线算齐后旧版本归档。`, 'subtle'),
    versions);
}

function nowLocalInput() {
  // 取北京时间的 datetime-local 值（yyyy-MM-ddTHH:mm）。
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date()).reduce((acc, part) => {
    if (part.type !== 'literal') acc[part.type] = part.value;
    return acc;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

// ---- 签到 ----

function checkinPanel(shiftId, checkins, plans, pools, refresh, message) {
  const active = plans.find(plan => plan.state === 'active');
  const poolOptions = [[OFFICE, '值班室'], ...pools.map(pool => [pool.code, `${pool.code} ${pool.name}`])];
  const form = el('form', {class: 'fields form-grid'},
    select('pool_code', '地点', poolOptions),
    select('kind', '事件', Object.entries(CHECKIN_KIND)),
    input('event_at', '签到时间（北京时间）', {type: 'datetime-local', value: nowLocalInput()}),
    input('note', '备注', {required: false}),
    button('提交签到', {type: 'submit', class: 'primary'}));
  form.addEventListener('submit', handler(form.querySelector('button'), message, async event => {
    const d = new FormData(event.target);
    const pool = d.get('pool_code') === OFFICE ? null : d.get('pool_code');
    await workspace.checkin({
      shift_id: shiftId, pool_code: pool, kind: d.get('kind'),
      event_at: `${d.get('event_at')}:00+08:00`, note: d.get('note') || null,
    });
    await refresh();
  }));
  const grid = table([
    {key: 'seq', label: '序号'},
    {label: '地点', render: row => row.pool_code || '值班室'},
    {label: '事件', render: row => CHECKIN_KIND[row.kind]},
    {label: '时间', render: row => date(row.event_at)},
    {key: 'actor', label: '签到人'},
    {key: 'note', label: '备注'},
  ], checkins, {caption: '签到流水（只追加，两班并发不漏记）', empty: '还没有签到。'});
  return panel('现场签到',
    p('两班可同时提交，后端按班次内单调序号落库；旧任务晚算完也不能覆盖新签到。', 'subtle'),
    active ? (session.canWrite ? form : p('只读角色可查看签到。', 'subtle')) : p('请先生成执行路线。', 'empty'),
    grid);
}

export async function patrolPage(route, refresh) {
  const message = feedback();
  const [pools, edges, shifts] = await Promise.all([
    workspace.patrolPools(), workspace.patrolEdges(), workspace.patrolShifts()]);
  const shiftId = route.id || shifts[0]?.id;
  if (!shiftId) {
    return el('div', {class: 'two-col'},
      el('div', {class: 'wide-panel'}, poolPanel(pools, refresh, message), edgePanel(edges, refresh, message)),
      el('aside', {class: 'sidebar-panel'}, shiftPanel(shifts, refresh, message)));
  }
  const shift = shifts.find(item => String(item.id) === String(shiftId));
  const [plans, checkins] = await Promise.all([
    workspace.patrolPlans(shiftId), workspace.patrolCheckins(shiftId)]);
  const orderedPools = [...pools].sort((a, b) => (a.trigger_date || '9999').localeCompare(b.trigger_date || '9999'));
  return el('div', {},
    el('div', {class: 'actions'}, link('← 班次列表', '#patrol', {class: 'quiet'}),
      link('打开巡查交班报告 →', `#handover/${shiftId}`, {class: 'quiet'})),
    el('div', {class: 'two-col'},
      el('div', {class: 'wide-panel'},
        routePanel(shiftId, plans, orderedPools, refresh, message),
        checkinPanel(shiftId, checkins, plans, orderedPools, refresh, message)),
      el('aside', {class: 'sidebar-panel'},
        panel(`班次 ${shift ? `${shift.shift_date} ${shift.team}` : shiftId}`,
          shift ? p(`${shift.start_clock}–${shift.end_clock} · ${shift.note || ''}`, 'subtle') : null,
          link('池位与路段台账', '#patrol', {class: 'quiet'})),
        poolPanel(pools, refresh, message), edgePanel(edges, refresh, message))));
}
