import {request, saveFile} from '../api/http.js';
import {session} from './session.js';

const call = (path, body) => request(path, {token:session.token, body});
export const workspace = {
  spec: null,
  datasets: [],
  runs: [],
  async init() { this.spec = await request('/spec'); },
  async refresh() { [this.datasets, this.runs] = await Promise.all([call('/datasets'), call('/runs')]); },
  getDataset: (id, offset = 0) => call(`/datasets/${id}?offset=${offset}`),
  getRun: id => call(`/runs/${id}`),
  upload: data => call('/datasets', data),
  start: data => call('/runs', data),
  review: (id, data) => call(`/runs/${id}/review`, data),
  audit: () => call('/audit'),
  schedules: () => call('/schedules'),
  deliveries: () => call('/deliveries'),
  schedule: data => call('/schedules', data),
  disable: id => call(`/schedules/${id}/disable`, {}),
  download: (path, filename) => saveFile(path, session.token, filename),
  // 巡查排班
  patrolPools: () => call('/patrol/pools'),
  savePool: data => call('/patrol/pools', data),
  patrolEdges: () => call('/patrol/edges'),
  saveEdge: data => call('/patrol/edges', data),
  patrolShifts: () => call('/patrol/shifts'),
  createShift: data => call('/patrol/shifts', data),
  patrolPlans: id => call(`/patrol/shifts/${id}/plans`),
  activatePlan: id => call(`/patrol/shifts/${id}/plan`, {}),
  replan: data => call('/patrol/replan', data),
  patrolCheckins: id => call(`/patrol/shifts/${id}/checkins`),
  checkin: data => call('/patrol/checkins', data),
  handover: id => call(`/patrol/shifts/${id}/handover`),
};
