import { ApiError } from "./api";

/** 没拿到明确拒绝时，保存可能已经完成；只读回，不重放提交。 */
export const isUnknownWrite = (error: unknown): boolean =>
  !(error instanceof ApiError) || error.status === 408 || error.status >= 500;

export async function readAfterWriteFailure(error: unknown, read: () => Promise<unknown>) {
  if (!isUnknownWrite(error)) return { unknown: false, checked: false, error: null };
  try {
    await read();
    return { unknown: true, checked: true, error: null };
  } catch (readError) {
    return { unknown: true, checked: false, error: readError };
  }
}
