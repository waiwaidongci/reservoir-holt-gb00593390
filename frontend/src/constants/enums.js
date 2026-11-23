export const ROLE = {admin:'复核主管', analyst:'分析员', viewer:'只读查看'};
export const STATUS = {queued:'排队中', running:'正在计算', complete:'计算完成', failed:'运行失败'};
export const REVIEW = {pending:'待复核', accepted:'已通过', returned:'已退回'};
export const PLAN_STATE = {
  planning:'计算中', active:'执行中', pending_replan:'待重排', archived:'已归档',
};
export const CHECKIN_KIND = {arrive:'到达', depart:'离开', return:'返回值班室'};
