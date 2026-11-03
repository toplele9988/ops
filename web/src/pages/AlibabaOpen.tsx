import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { copyText } from "../api";
import { alibabaOpenAuthorization, alibabaOpenCandidates, alibabaOpenStatus, authorizeAlibabaOpen, claimAlibabaOpenCandidate, pollAlibabaOpen, saveAlibabaOpenSettings, type AlibabaOpenStatus, type AlibabaOrderCandidate } from "../alibabaOpen";
import { formatTime } from "../format";
import { isUnknownWrite } from "../readback";
import { Chip, Empty, Skeleton } from "../ui/bits";
import { ReadFailure, useWriteRecovery, WriteRecovery } from "../ui/ReadState";
import { useToast } from "../ui/Toast";

function Settings({ status, disabled, onError }: { status: AlibabaOpenStatus; disabled: boolean; onError: (error: Error) => Promise<void> }) {
  const client = useQueryClient();
  const toast = useToast();
  const [paused, setPaused] = useState(status.paused);
  const [limit, setLimit] = useState(String(status.daily_limit));
  const save = useMutation({
    mutationFn: () => saveAlibabaOpenSettings(paused, limit), retry: false,
    onSuccess: () => { client.invalidateQueries({ queryKey: ["alibaba-open-status"] }); toast("读取设置已保存"); },
    onError,
  });
  return <form onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
    <div className="form-grid"><label><span>每日接口调用上限</span><input type="number" min="1" max="100000" step="1" required value={limit} onChange={(e) => setLimit(e.target.value)} /></label><label><span>暂停读取</span><select value={paused ? "1" : "0"} onChange={(e) => setPaused(e.target.value === "1")}><option value="1">暂停，不读取订单</option><option value="0">允许手动读取</option></select></label></div>
    <p className="t-foot" style={{ marginTop: 10 }}>读取列表、订单详情及换取或刷新授权都会计入上限。这里不创建定时任务。</p>
    <button className="btn" type="submit" disabled={disabled || save.isPending}>{save.isPending ? "正在保存…" : "确认保存读取设置"}</button>
  </form>;
}

function Candidate({ row, disabled, onError }: { row: AlibabaOrderCandidate; disabled: boolean; onError: (error: Error) => Promise<void> }) {
  const client = useQueryClient();
  const toast = useToast();
  const [dealId, setDealId] = useState("");
  const paid = row.payment_status === "paid";
  const paymentStatus = paid ? "平台显示已付款" : row.payment_status === "unpaid" ? "平台显示尚未付款" : "付款状态待核对";
  const nextStep = paid ? "请核对买家、订单及实际到账，再到案子里点「确认收款」。" : "先核对原付款记录和平台当前状态，系统未改动已经登记的款项；不要根据这条候选新增或确认收款。";
  const claim = useMutation({
    mutationFn: () => claimAlibabaOpenCandidate(row.order_id, dealId), retry: false,
    onSuccess: () => { client.invalidateQueries({ queryKey: ["alibaba-open-candidates"] }); toast("候选已关联到案子，请继续核对订单与付款记录"); },
    onError,
  });
  const copy = async () => {
    const text = `阿里订单：${row.order_id}\n当前付款状态：${paymentStatus}\n订单总额（实收待核对）：${row.amount} ${row.currency}\n买家标识：${row.buyer_id}\n${paid ? "平台记录的付款时间" : "平台原付款时间（仅供核对原记录）"}：${formatTime(row.paid_at_ms) || "待核对"}\n订单总额不代表实际收到的款项。${nextStep}`;
    const copied = await copyText(text);
    toast(copied ? "核对资料已复制，请到案子里核对付款记录" : "没有复制成功，请手动选取订单资料", copied ? "ok" : "error");
  };
  return <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
    <div className="row-main" style={{ whiteSpace: "normal", minWidth: 180 }}><div className="row-title">订单 {row.order_id} <Chip tone={row.claimed ? "blue" : "orange"}>{row.claimed ? "已关联，待核款" : "待认领"}</Chip></div><div className="row-sub" style={{ whiteSpace: "normal" }}>订单总额（实收待核对）{row.amount} {row.currency} · 买家标识 {row.buyer_id}</div><div className="row-sub"><Chip tone={paid ? "blue" : "orange"}>{paymentStatus}</Chip>{paid && ` · ${formatTime(row.paid_at_ms) || "付款时间待核对"}`}</div><p className="t-foot">订单总额不代表实收。这条候选没有确认收款，也没有将案子标为成交。</p><div className={paid ? "t-foot" : "callout"} role={paid ? "note" : "alert"}>{nextStep}</div><div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}><Link className="btn sm" to={`/deals/${row.deal_id}`}>打开案子核对付款记录</Link><button type="button" className="btn sm" onClick={copy}>复制收款核对资料</button></div>
      {!row.claimed && <form style={{ marginTop: 12 }} onSubmit={(event) => { event.preventDefault(); claim.mutate(); }}><label><span>这笔订单已有案子？填写案子编号后关联</span><input inputMode="numeric" pattern="[1-9][0-9]*" required maxLength={19} value={dealId} onChange={(e) => setDealId(e.target.value)} /></label><button type="submit" className="btn sm" disabled={disabled || claim.isPending || !dealId}>{claim.isPending ? "正在关联…" : "确认关联到这个案子"}</button><p className="t-foot">系统不会按买家名称猜归属，请先打开目标案子核对编号。</p></form>}
    </div>
  </div>;
}

export function AlibabaOpen() {
  const client = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["alibaba-open-status"], queryFn: alibabaOpenStatus, retry: false });
  const candidates = useQuery({ queryKey: ["alibaba-open-candidates"], queryFn: alibabaOpenCandidates, retry: false });
  const authorization = useQuery({ queryKey: ["alibaba-open-authorization"], queryFn: alibabaOpenAuthorization, enabled: false, retry: false });
  const [code, setCode] = useState("");
  const [authorizationUnknown, setAuthorizationUnknown] = useState(false);
  const readback = async () => {
    const results = await Promise.all([status.refetch(), candidates.refetch()]);
    for (const result of results) if (result.error) throw result.error;
  };
  const recovery = useWriteRecovery(readback);
  const authorize = useMutation({
    mutationFn: authorizeAlibabaOpen, retry: false,
    onSuccess: () => { client.invalidateQueries({ queryKey: ["alibaba-open-status"] }); toast("授权已保存，可以手动读取订单"); },
    onError: async (error: Error) => {
      if (isUnknownWrite(error)) setAuthorizationUnknown(true);
      await recovery.fail(error);
    },
  });
  const poll = useMutation({ mutationFn: pollAlibabaOpen, retry: false,
    onSuccess: () => { client.invalidateQueries({ queryKey: ["alibaba-open-status"] }); client.invalidateQueries({ queryKey: ["alibaba-open-candidates"] }); toast("本次读取已结束，请查看结果和成交候选"); },
    onError: recovery.fail,
  });
  const busy = authorize.isPending || poll.isPending || recovery.blocked;
  const unavailable = busy || status.isError || !status.data;
  const url = authorization.data?.url;
  const safeUrl = url && /^https:\/\//i.test(url) ? url : null;
  const data = status.data;
  return <>
    <div className="page-head"><div><h1 className="t-large">阿里订单接入</h1><p>读取平台订单，整理成待核对的成交候选。平台显示已付款后，仍由你核对实际到账并在案子里确认收款。</p></div><button type="button" className="btn" disabled={status.isFetching || candidates.isFetching} onClick={() => { status.refetch(); candidates.refetch(); }}>重新读取状态</button></div>
    <WriteRecovery recovery={recovery} subject="阿里订单操作" />
    <div className="callout" role="note">授权入口与回调规则仍待总指挥核对。管理员确认前，请先保留暂停读取；本页的假服务验证不代表真实订单已经可用。</div>
    {status.isLoading && <Skeleton lines={3} />}
    {status.isError && <ReadFailure subject="阿里订单接入状态" error={status.error} retry={status.refetch} busy={status.isFetching} />}
    {data && !status.isError && <section className="block"><h2 className="group-title">当前状态 <Chip tone={data.authorized ? "green" : "orange"}>{data.authorized ? "已授权" : "尚未授权"}</Chip></h2><div className="group" style={{ padding: 16 }}><p>{data.configured ? "应用凭据已配置。" : "应用凭据尚未配置或权限不正确，请管理员先完成配置。"}{data.token_expires_in != null && ` 授权剩余约 ${Math.max(0, Math.floor(data.token_expires_in / 60))} 分钟。`}</p><p>{data.next_step}</p><p className="t-foot">最近读取：{formatTime(data.last_poll_at) || "还没有读取"} · {data.last_poll_result || "尚无结果"}</p><p className="t-foot">今天已调用 {data.calls_today} 次，上限 {data.daily_limit} 次。{data.next_poll_at ? ` 下次允许读取：${formatTime(data.next_poll_at)}。` : ""}{data.paused ? " 当前已暂停读取。" : ""}</p><button type="button" className="btn primary" disabled={unavailable || !data.configured || !data.authorized || data.paused} onClick={() => poll.mutate()}>{poll.isPending ? "正在读取订单…" : "确认读取一次订单"}</button></div></section>}
    <section className="block"><h2 className="group-title">浏览器授权</h2><div className="group" style={{ padding: 16 }}><ol className="plain-list"><li>请管理员先在服务器保存应用凭据并检查权限。</li><li>点击「生成授权链接」，在浏览器打开链接，用老板的账号授权。</li><li>授权结束后，从地址栏只复制 code 的值，粘贴到下面并点击「确认保存授权」。不要把整个网址或 code 发到聊天里。</li></ol><button type="button" className="btn" disabled={unavailable || !data?.configured || authorization.isFetching} onClick={() => authorization.refetch()}>{authorization.isFetching ? "正在生成…" : "生成授权链接"}</button>{authorization.isError && <p role="alert" className="t-foot">授权链接暂时无法生成，请确认应用凭据已配置，再重新生成。</p>}{safeUrl && <p><a href={safeUrl} target="_blank" rel="noreferrer noopener">在浏览器打开授权页面</a></p>}{authorizationUnknown && <div className="callout" role="alert">上次保存授权的结果还不明确，系统不会重试同一个 code。先重新读取状态；若仍未授权，请重新打开授权页面获取新的 code。</div>}<form style={{ marginTop: 12 }} onSubmit={(event) => { event.preventDefault(); const value = code; setCode(""); authorize.mutate(value); }}><label><span>授权 code</span><input type="password" name="authorization-code" autoComplete="off" spellCheck={false} maxLength={2048} value={code} onChange={(e) => setCode(e.target.value)} /></label><button type="submit" className="btn primary" disabled={unavailable || !data?.configured || !code.trim() || authorizationUnknown}>{authorize.isPending ? "正在保存授权…" : "确认保存授权"}</button></form>{authorizationUnknown && !recovery.blocked && <button type="button" className="btn sm" onClick={() => { setCode(""); setAuthorizationUnknown(false); authorization.refetch(); }}>已核对状态，重新开始浏览器授权</button>}</div></section>
    {data && !status.isError && <section className="block"><h2 className="group-title">读取开关与上限</h2><div className="group" style={{ padding: 16 }}><Settings key={`${data.paused}-${data.daily_limit}`} status={data} disabled={unavailable} onError={recovery.fail} /></div></section>}
    <section className="block"><h2 className="group-title">成交候选</h2><p className="t-foot">只作为收款提示和填写资料。待认领候选不带客户归属，不会替你确认收款。</p><div className="group">{candidates.isLoading && <Skeleton lines={3} />}{candidates.isError && <ReadFailure subject="成交候选" error={candidates.error} retry={candidates.refetch} busy={candidates.isFetching} />}{!candidates.isError && candidates.data?.items.length === 0 && <Empty title="还没有成交候选">完成授权并手动读取订单；平台订单是否可读取，仍需上线前核对应用权限。</Empty>}{!candidates.isError && candidates.data?.items.map((row) => <Candidate key={row.order_id} row={row} disabled={unavailable || candidates.isFetching} onError={recovery.fail} />)}</div></section>
  </>;
}
