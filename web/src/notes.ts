// The API can word one warning twice and mention channels that are only reserved; show each point once.
export const dedupe = (notes: string[]) =>
  notes.filter((note, i) => !note.includes("暂无可用数据") && !notes.some((other, j) => j !== i && other.length > note.length && other.includes(note)));

export const CHANNEL_NAME: Record<string, string> = { alibaba: "阿里国际站", wechat: "微信", whatsapp: "WhatsApp", website: "独立站" };
