import { useEffect, useState } from "react";

// Only countries with a single civil timezone are inferred. Multi-zone countries
// (US, CA, AU, BR, RU, etc.) require an explicitly confirmed IANA zone.
const COUNTRIES: Record<string, string> = {};
for (const [names, zone] of [
  ["CN,China,中国", "Asia/Shanghai"], ["GB,UK,United Kingdom,英国", "Europe/London"],
  ["DE,Germany,德国", "Europe/Berlin"], ["JP,Japan,日本", "Asia/Tokyo"],
  ["KR,South Korea,韩国", "Asia/Seoul"], ["IN,India,印度", "Asia/Kolkata"],
  ["SG,Singapore,新加坡", "Asia/Singapore"], ["AE,United Arab Emirates,阿联酋", "Asia/Dubai"],
  ["SA,Saudi Arabia,沙特阿拉伯", "Asia/Riyadh"], ["ZA,South Africa,南非", "Africa/Johannesburg"],
  ["TH,Thailand,泰国", "Asia/Bangkok"], ["VN,Vietnam,越南", "Asia/Ho_Chi_Minh"],
  ["PH,Philippines,菲律宾", "Asia/Manila"], ["TR,Turkey,土耳其", "Europe/Istanbul"],
  ["IT,Italy,意大利", "Europe/Rome"], ["PL,Poland,波兰", "Europe/Warsaw"],
] as const) for (const name of names.split(",")) COUNTRIES[name.toLowerCase()] = zone;

export function counterpartZone(country?: string | null, timezone?: string | null): string | undefined {
  const zone = timezone?.trim() || COUNTRIES[country?.trim().toLowerCase() ?? ""];
  if (!zone) return;
  try { new Intl.DateTimeFormat("zh-CN", { timeZone: zone }).format(); return zone; } catch { return; }
}
export function CounterpartTime({ country, timezone }: { country?: string | null; timezone?: string | null }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const timer = window.setInterval(() => setNow(new Date()), 60_000); return () => window.clearInterval(timer); }, []);
  const zone = counterpartZone(country, timezone);
  if (!zone) return null;
  const value = new Intl.DateTimeFormat("zh-CN", { timeZone: zone, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short" }).format(now);
  return <p className="t-foot" aria-label="对方当地时间">对方当地时间：<time dateTime={now.toISOString()}>{value}</time>（{zone}）</p>;
}
