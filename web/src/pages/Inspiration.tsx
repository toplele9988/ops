import { PageIntro } from "./overview/PageIntro";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { get, postForm, qs } from "../api";
import { formatTime } from "../format";
import { CONFIDENCE, ITEM_KIND, RELEVANCE, STATUS } from "../labels";
import { Chip, Disclosure, Empty, Pick, SearchField, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { useToast } from "../ui/Toast";

interface Item { name: string; kind: string; url?: string | null }
interface Card {
  card_id: string; author: string; text: string; tags: string[]; urls: string[]; summary?: string; items: Item[];
  usefulness?: string; how_to_use?: string; confidence?: string; relevance: string; status: string; note?: string;
  first_seen?: number; times_seen?: number;
}
interface Counts { total: number; relevance: Record<string, number>; status: Record<string, number> }
interface Page { items: Card[]; total: number; page: number; page_size: number; counts: Counts; ready: boolean }

const PAGE_SIZE = 20;
const NO_USEFULNESS = "与当前业务关系不大";

function Note({ card, onSave }: { card: Card; onSave: (note: string) => void }) {
  const [note, setNote] = useState(card.note ?? "");
  return (
    <label style={{ display: "block", marginTop: 12 }}>
      <span className="t-foot">你的备注</span>
      <textarea
        className="textarea"
        name="note"
        rows={2}
        maxLength={500}
        autoComplete="off"
        placeholder="打算用在哪、谁来试…"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        onBlur={() => note !== (card.note ?? "") && onSave(note)}
      />
    </label>
  );
}

function Detail({ card, onSave }: { card: Card; onSave: (fields: Record<string, string>) => void }) {
  return (
    <div style={{ display: "grid", gap: 10, maxWidth: "70ch" }}>
      {card.usefulness && card.usefulness !== NO_USEFULNESS && (
        <p><b>对我们有什么用　</b>{card.usefulness}</p>
      )}
      <p>
        <b>怎么用　</b>{card.how_to_use || "还没有整理"}
        {card.confidence && <> <Chip tone={CONFIDENCE[card.confidence]?.tone}>{CONFIDENCE[card.confidence]?.text}</Chip></>}
      </p>
      {card.items.length > 0 && (
        <p>
          <b>提到的工具　</b>
          {card.items.map((item) => (
            <span key={item.name} style={{ marginRight: 8 }}>{item.name}<span className="t-cap">（{ITEM_KIND[item.kind] ?? item.kind}）</span></span>
          ))}
        </p>
      )}
      <div>
        <div className="t-foot" style={{ marginBottom: 4 }}>卡片原文</div>
        <p className="t-sub" style={{ whiteSpace: "pre-wrap" }}>{card.text || "（卡片没有文字描述）"}</p>
      </div>
      {card.urls.length > 0 && <p className="t-foot">卡片里的链接：{card.urls.join("　")}</p>}
      <Note card={card} onSave={(note) => onSave({ note })} />
    </div>
  );
}

export function Inspiration() {
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const client = useQueryClient();
  const q = params.get("q") ?? "";
  const relevance = params.get("relevance") ?? "";
  const status = params.get("status") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);

  // Filters and page live in the URL so refresh, bookmarks and shared links restore the same view.
  const setFilter = (changes: Record<string, string>, keepPage = false) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) (value ? next.set(key, value) : next.delete(key));
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  };

  const query = useQuery({
    queryKey: ["inspiration", q, relevance, status, page],
    queryFn: () => get<Page>(`/api/v1/inspiration${qs({ q, relevance, status, page, page_size: PAGE_SIZE })}`),
    placeholderData: keepPreviousData,
  });

  const save = useMutation({
    mutationFn: ({ id, fields }: { id: string; fields: Record<string, string> }) =>
      postForm(`/api/v1/inspiration/${id}/update`, fields),
    onSuccess: () => { toast("已保存"); client.invalidateQueries({ queryKey: ["inspiration"] }); },
    onError: (error: Error) => toast(`没保存成功：${error.message}`, "error"),
  });

  const data = query.data;
  const counts = data?.counts;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const options = [
    { value: "", label: "全部", count: counts?.total },
    { value: "ai_dev", label: "AI 开发", count: counts?.relevance.ai_dev },
    { value: "industry", label: "同行与产品", count: counts?.relevance.industry },
    { value: "business", label: "外贸运营", count: counts?.relevance.business },
    { value: "other", label: "无关", count: counts?.relevance.other },
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">AI 开发灵感库</h1>
          <p>你转发给运营微信的视频号卡片，自动整理在这里：它讲了什么、对我们有没有用、要不要试。目前只读得到卡片上的文字，视频本身还没下载，所以「怎么用」有时只能写成需要看原视频。</p>
        </div>
      </div>
      <PageIntro text="核对灵感来源，记录对当前生意有用的改进。" />

      <div className="toolbar">
        <Segmented label="按类型筛选" value={relevance} options={options} onChange={(value) => setFilter({ relevance: value })} />
      </div>
      <div className="toolbar">
        <div className="grow"><SearchField label="搜索卡片" placeholder="搜作者、内容、工具名…" value={q} onSearch={(value) => setFilter({ q: value })} /></div>
        <Pick label="按状态筛选" value={status} onChange={(value) => setFilter({ status: value })}
          options={[{ value: "", label: "全部状态" }, ...Object.entries(STATUS).map(([value, label]) => ({ value, label }))]} />
        {data && <span className="t-foot num">共 {data.total} 张</span>}
      </div>

      {query.isError && <Empty title="没能读到灵感库">{(query.error as Error).message}</Empty>}
      {query.isLoading && <Skeleton lines={5} />}
      {data && data.items.length === 0 && (
        <div className="group">
          <Empty
            title={data.ready === false ? "还没有数据" : "没有符合条件的卡片"}
            action={q || relevance || status ? <button type="button" className="btn" onClick={() => setParams({}, { replace: true })}>清除筛选</button> : undefined}
          >
            {data.ready === false ? "后台的整理任务还没跑过。" : "换个关键词或类型试试。"}
          </Empty>
        </div>
      )}
      {data && data.items.length > 0 && (
        <div className="group" style={{ opacity: query.isPlaceholderData ? 0.6 : 1, transition: "opacity var(--dur-1)" }}>
          {data.items.map((card) => (
            <Disclosure
              key={card.card_id}
              aside={
                <Pick pill label="评估状态" value={card.status}
                  onChange={(value) => save.mutate({ id: card.card_id, fields: { status: value } })}
                  options={Object.entries(STATUS).map(([value, label]) => ({ value, label }))} />
              }
              head={
                <div>
                  <div>
                    <div className="row-title">{card.summary || (card.text ? card.text.slice(0, 60) : "（没有文字描述）")}</div>
                    <div className="row-sub" style={{ display: "flex", flexWrap: "wrap", gap: "2px 10px", alignItems: "center" }}>
                      <Chip tone={RELEVANCE[card.relevance]?.tone}>{RELEVANCE[card.relevance]?.text ?? card.relevance}</Chip>
                      <span>{card.author}</span>
                      <span className="num">{formatTime(card.first_seen)}</span>
                      {card.items.slice(0, 3).map((item) => <span key={item.name}>· {item.name}</span>)}
                    </div>
                  </div>
                </div>
              }
            >
              <Detail card={card} onSave={(fields) => save.mutate({ id: card.card_id, fields })} />
            </Disclosure>
          ))}
        </div>
      )}
      {data && pages > 1 && (
        <div className="pager">
          <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setFilter({ page: String(page - 1) }, true)}>上一页</button>
          <span className="num">第 {page} / {pages} 页</span>
          <button type="button" className="btn sm" disabled={page >= pages} onClick={() => setFilter({ page: String(page + 1) }, true)}>下一页</button>
        </div>
      )}
    </>
  );
}
