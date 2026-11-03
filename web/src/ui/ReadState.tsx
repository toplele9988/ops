import { useState } from "react";
import { ApiError } from "../api";
import { isUnknownWrite, readAfterWriteFailure } from "../readback";
import { useToast } from "./Toast";

export function ReadFailure({ subject, error, retry, busy = false }: { subject: string; error: unknown; retry: () => unknown; busy?: boolean }) {
  const reason = error instanceof ApiError && (error.status === 401 || error.status === 403)
    ? "当前登录没有读取权限。请重新登录或联系管理员确认权限，再重新读取。"
    : error instanceof ApiError && error.status === 404
      ? "这项记录未找到。请核对案子编号，再重新读取。"
      : error instanceof ApiError && error.status >= 500
        ? "服务暂时不可用。请稍后重新读取。"
        : "连接中断或读取超时。请检查连接后重新读取。";
  return <div className="callout" role="alert"><b>没能读到{subject}</b><p>{reason}读取失败不代表没有记录，请先核对，暂时不要重复登记。</p><button type="button" className="btn sm" disabled={busy} onClick={() => retry()}>{busy ? "正在读取…" : "重新读取"}</button></div>;
}

export function useWriteRecovery(readback: () => Promise<unknown>) {
  const toast = useToast();
  const [state, setState] = useState<{ checking: boolean; checked: boolean; error: unknown } | null>(null);
  const recheck = async () => {
    setState({ checking: true, checked: false, error: null });
    const result = await readAfterWriteFailure(new Error(), readback);
    setState({ checking: false, checked: result.checked, error: result.error });
  };
  const fail = async (error: Error) => {
    if (!isUnknownWrite(error)) { toast(error.message, "error"); return; }
    await recheck();
  };
  return { state, blocked: state != null, fail, recheck, clear: () => setState(null) };
}

export function WriteRecovery({ recovery, subject }: { recovery: ReturnType<typeof useWriteRecovery>; subject: string }) {
  const s = recovery.state;
  if (!s) return null;
  return <div className="callout" role="alert"><b>{subject}结果还需核对</b><p>{s.checking ? "正在重新读取记录，请先等一下。" : s.checked ? "已经重新读取。请对照下面的记录核对是否已保存；即使暂时没看到，也不要直接重复提交。" : "重新读取也没有成功。请恢复读取后核对记录，再决定下一步。"}系统没有重新提交。</p>{s.error != null && <ReadFailure subject={subject + "的核对记录"} error={s.error} retry={recovery.recheck} />}{!s.checking && <button type="button" className="btn sm" onClick={recovery.recheck}>重新读取核对</button>}{s.checked && <button type="button" className="btn sm" onClick={recovery.clear}>我已核对，继续操作</button>}</div>;
}
