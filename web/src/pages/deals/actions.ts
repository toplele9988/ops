import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { postForm } from "../../api";
import { uuid } from "../../lib_uuid";
import { useToast } from "../../ui/Toast";

const pending = new Map<string, string>();
export async function operationForm<T>(path: string, fields: Record<string, string>): Promise<T> {
  const signature = JSON.stringify([path, fields]);
  const key = pending.get(signature) ?? uuid();
  pending.set(signature, key);
  const result = await postForm<T>(path, { ...fields, operation_key: key });
  pending.delete(signature);
  return result;
}

/** A lost response retains the same key; a deliberate later action gets a new key. */
export function useDealAction() {
  const client = useQueryClient();
  const toast = useToast();
  const lock = useRef(false);
  const keys = useRef(new Map<string, string>());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const run = async <T = { id: number },>(path: string, fields: Record<string, string>, feedback: string): Promise<T | undefined> => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setMessage("");
    const signature = JSON.stringify([path, fields]);
    const key = keys.current.get(signature) ?? uuid();
    keys.current.set(signature, key);
    try {
      const result = await postForm<T>(`/api/v1/${path}`, { ...fields, operation_key: key });
      keys.current.delete(signature);
      await Promise.all(["deal", "deals", "deals-summary", "quote", "quotes", "payments", "fulfilment", "fulfil-board"].map((k) => client.invalidateQueries({ queryKey: [k] })));
      setMessage(feedback); toast(feedback); return result;
    } catch (e) { setError((e as Error).message + "；请先刷新核对。重试相同内容不会重复记账。"); }
    finally { lock.current = false; setBusy(false); }
  };
  return { run, busy, message, error };
}

export type Action = ReturnType<typeof useDealAction>;
