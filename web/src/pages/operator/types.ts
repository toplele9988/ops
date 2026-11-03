export const kinds: Record<string, string> = {
  ask_supplier: '问供应商资料、价格与交期', ask_forwarder: '问货代运费', reply_customer: '回复客户',
  quote_customer: '给客户报价', draft_pi: '起草 PI', draft_order: '起草订单', shipping: '联系货代安排发货',
  schedule_shipping: '安排发货节点', create_deal: '新建案子', advance_deal: '推进案子', update_profile: '记录档案更新',
  ask_owner: '请老板核对', confirm_payment: '人工核对收款',
};
export const typeNames: Record<string, string> = {
  customer_project: '工程客户', customer_wholesale: '批发客户', customer_end: '终端客户', customer_other: '其他客户',
  supplier_product: '产品供应商', supplier_logistics: '物流供应商', supplier_support: '配套供应商',
};
export const states: Record<string, string> = {
  dry_run: '演练草稿', pending: '待你确认', scheduled: '已排队', ready: '待执行', sending: '发送中', sent: '已发送',
  done: '已完成', blocked: '资料待核对', unknown: '结果未知，请核对原渠道', rejected: '已拒绝', duplicate: '重复内容已拦截', failed: '发送失败',
};
export const reasons: Record<string, string> = {
  master_off: '总开关已关闭', rehearsal: '当前为演练模式', switch_off: '未获得完整自动授权',
  recipient_night: '对方深夜，等待当地早上', timezone_required: '请先核对对方时区', language_required: '请先核对对方语言',
  rate_or_interval: '等待发送间隔或联系人额度恢复', channel_unavailable: '请到原渠道人工发送',
  payment_always_manual: '收款必须到案子页人工确认', owner_required: '需要老板处理',
  unsourced_content: '正文含有未核准内容', source_unusable: '事实来源不可用',
  quote_changed_or_expired: '报价或成本已改变、过期，请重新起草', identity_or_profile_changed: '联系人资料已变化，请重新起草',
  document_review_required: '请先在案子里核对单据条款', document_changed: '单据已改变，请重新起草',
  context_unavailable: '暂时读不到业务资料', same_content_or_unknown: '同一内容曾尝试发送，不能重复发送',
  owner_rejected: '老板已拒绝', owner_edited: '已修改，等待确认', master_stopped: '总开关已停用',
  old_rehearsal_requires_review: '历史演练草稿须逐条确认', human_payment_evidence_required: '须先人工确认收款',
};
export type Profile = { type: string; timezone?: string; language?: string; tone?: string; hourly_limit?: number; daily_limit?: number; interval_seconds?: number; name?: string; channel?: string };
export type Settings = { enabled: boolean; dry_run: boolean; levels: Record<string, boolean>; types: Record<string, boolean>; contacts: Record<string, boolean> };
export type Action = { id: number; event_id: string; kind: string; contact_key: string; level: number; state: string; reason: string; due_at: number | null; created_at: number; dry_run?: number; rehearsal?: { who: string; type: string; trigger: string; why: string; sources: { label: string; ref: string; conversation_key?: string; at?: string }[]; would_send_at: number | null; timing_reason: string }; data: { rehearsal_only?: boolean; parts: string[]; fact_ids: string[]; payload?: Record<string, unknown>; context?: { contacts?: Record<string, Profile>; deal?: { id: number; title: string } } } };
export type Decision = { id: number; event_id: string | null; action_id: number | null; at: number; phase: string; data: unknown };
export type Snapshot = { settings: Settings; profiles: Record<string, Profile>; actions: Action[]; decisions: Decision[]; types: string[]; transport: string; health?: {component: string; ok: number; at: number; error_type?: string; failures: number}[]; unresolved_events: { id: string; state: string }[] };
export const beijing = (at: number) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium' }).format(at * 1000);
