import { useState } from "react";
import data from "../data/requirements.json";
import type { Tone } from "../labels";
import { Chip, Disclosure } from "../ui/bits";
import { Segmented } from "../ui/Segmented";

type Status = "done" | "partial" | "todo" | "decide";
interface Item { id: string; ask: string; status: Status; now: string; accept: string; next: string }
interface Group { id: string; title: string; items: Item[] }

const STATUS: Record<Status, { text: string; tone: Tone }> = {
  done: { text: "已做好", tone: "green" },
  partial: { text: "做了一部分", tone: "blue" },
  todo: { text: "还没做", tone: "plain" },
  decide: { text: "等你拍板", tone: "orange" },
};

// Everything you asked for, with how to check it. New asks are added here first, then built.
export function Requirements() {
  const groups = data.groups as Group[];
  const all = groups.flatMap((g) => g.items);
  const [filter, setFilter] = useState<"" | Status>("");
  const count = (s: Status) => all.filter((i) => i.status === s).length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">需求与验收</h1>
          <p>{data.note}更新于 {data.updated}。</p>
        </div>
      </div>
      <div className="block">
        <h2 className="group-title">总工程：分批做完<span className="count">每一批上线前都要过全页面自动检查</span></h2>
        <div className="group">
          {(data as unknown as { batches: { id: string; title: string; status: string; summary: string }[] }).batches.map((b) => (
            <div className="row" key={b.id} style={{ alignItems: "flex-start" }}>
              <span className="t-cap num" style={{ width: 22, marginTop: 3 }}>{b.id}</span>
              <div className="row-main"><div className="row-title">{b.title}</div><div className="row-sub">{b.summary}</div></div>
              <Chip tone={b.status === "done" ? "green" : b.status === "doing" ? "blue" : "plain"}>{b.status === "done" ? "已完成" : b.status === "doing" ? "进行中" : "未开始"}</Chip>
            </div>
          ))}
        </div>
      </div>
      <h2 className="group-title" style={{ marginTop: 28 }}>你说过的每一条要求</h2>
      <div className="toolbar">
        <Segmented<"" | Status>
          label="按状态筛选" value={filter} onChange={setFilter}
          options={[
            { value: "", label: "全部", count: all.length },
            { value: "decide", label: "等你拍板", count: count("decide") },
            { value: "partial", label: "做了一部分", count: count("partial") },
            { value: "todo", label: "还没做", count: count("todo") },
            { value: "done", label: "已做好", count: count("done") },
          ]}
        />
      </div>
      {groups.map((g) => {
        const items = g.items.filter((i) => !filter || i.status === filter);
        if (items.length === 0) return null;
        return (
          <div className="block" key={g.id}>
            <h2 className="group-title">{g.title}<span className="count num">{items.length}</span></h2>
            <div className="group">
              {items.map((item) => (
                <Disclosure
                  key={item.id}
                  aside={<div style={{ padding: "2px 0" }}><Chip tone={STATUS[item.status].tone}>{STATUS[item.status].text}</Chip></div>}
                  head={
                    <div>
                      <div className="row-title"><span className="t-cap num" style={{ marginRight: 8 }}>{item.id}</span>{item.ask}</div>
                    </div>
                  }
                >
                  <dl className="kv" style={{ gridTemplateColumns: "84px 1fr", marginTop: 0 }}>
                    <dt>现在的情况</dt><dd>{item.now}</dd>
                    <dt>怎么验收</dt><dd>{item.accept}</dd>
                    {item.next && (<><dt>下一步</dt><dd>{item.next}</dd></>)}
                  </dl>
                </Disclosure>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}
