export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const csrf = (): string => {
  const value = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ?? "";
  return value === "__CSRF__" ? "" : value;
};

async function parse<T>(response: Response): Promise<T> {
  // An expired session answers with the login page; send people there instead of failing silently.
  if (response.redirected && response.url.includes("/login")) {
    window.location.assign("/login");
    throw new ApiError(401, "登录已过期，请重新登录");
  }
  if (!response.ok) {
    let message = "请求没有成功，请稍后再试";
    try {
      message = (await response.json()).error ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export const get = <T,>(url: string): Promise<T> => fetch(url, { credentials: "same-origin" }).then((r) => parse<T>(r));

export const postForm = <T,>(url: string, fields: Record<string, string>): Promise<T> =>
  fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": csrf() },
    body: new URLSearchParams(fields),
  }).then((r) => parse<T>(r));

export const qs = (params: Record<string, string | number | undefined>): string => {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") out.set(key, String(value));
  const text = out.toString();
  return text ? `?${text}` : "";
};

/** Clipboard API needs a secure context; the LAN console is plain http, so fall back to a hidden textarea. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

/** Read-only projection of unsendable dry-run tables; no mutation is exposed. */
export interface OpsDraft { id: number; source: "inbox" | "parties"; conversation_key: string; text: string; state: string; dry_run: boolean; created_at: number | string; reviewed_at: string | null }
export const readOpsDrafts = () => get<{ items: OpsDraft[]; dry_run: boolean }>("/api/v1/ops/drafts");
