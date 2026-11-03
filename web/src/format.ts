const DATE = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "long" });
const TIME = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
const toDate = (value: number | string) => new Date(typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : /^\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(value) ? value.replace(" ", "T") + "Z" : value);

export const today = () => DATE.format(new Date());
export const formatTime = (value?: number | string | null) => {
  if (value === undefined || value === null || value === "") return "";
  const date = toDate(value);
  return Number.isNaN(date.getTime()) ? "" : TIME.format(date);
};

/** "3 天前" style, falling back to the date after a week. */
export const ago = (value?: number | string | null) => {
  if (value === undefined || value === null || value === "") return "";
  const seconds = (Date.now() - toDate(value).getTime()) / 1000;
  if (seconds < 90) return "刚刚";
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`;
  if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} 天前`;
  return formatTime(value);
};

export const percent = (rate: number) => `${Math.round(rate * 100)}%`;

/** Long calendar date for day separators; accepts ISO strings or epoch seconds like the rest of the API. */
export const longDate = (value: number | string) => {
  const date = toDate(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "long", day: "numeric" });
};

export const beijingDate = () => new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
