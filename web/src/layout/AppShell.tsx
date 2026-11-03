import { useQuery } from "@tanstack/react-query";
import { Bell, ExternalLink, Menu, Search } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router";
import { get } from "../api";
import { NAV, areaOf, findNav, type Area } from "../nav";

const HOME: Record<Area, string> = { front: "/", back: "/system" };

interface InboxSummary { ready: boolean; waiting_total: number; draft_new: number }

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const area = areaOf(location.pathname);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const summary = useQuery({ queryKey: ["inbox-summary"], queryFn: () => get<InboxSummary>("/api/v1/inbox/summary"), refetchInterval: 60_000, retry: false });
  const waiting = summary.data?.ready ? summary.data.waiting_total : 0;
  const current = findNav(location.pathname);

  useEffect(() => setOpen(false), [location.pathname]);

  const items = NAV.filter((item) => item.area === area);
  const live = items;
  const groups = live.reduce<Record<string, typeof live>>((acc, item) => ((acc[item.group] ??= []).push(item), acc), {});

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const q = search.trim();
    if (q) navigate(`/inbox?q=${encodeURIComponent(q)}`);
  };

  const entry = (item: (typeof NAV)[number]) => {
    const Icon = item.icon;
    const props = {
      className: "nav-item",
      "aria-current": current?.key === item.key ? ("page" as const) : undefined,
    };
    const body = (
      <>
        <Icon size={18} aria-hidden="true" />
        <span className="grow">{item.label}{item.note && <small className="nav-note">{item.note}</small>}</span>
        {item.key === "inbox" && waiting > 0 && <span className="badge num" aria-label={`${waiting} 个待回复`}>{waiting}</span>}
        {item.kind === "external" && <ExternalLink size={13} className="ext" aria-label="在原控制台打开" />}
      </>
    );
    return item.kind === "external" ? (
      <a key={item.key} href={item.path} {...props}>{body}</a>
    ) : (
      <Link key={item.key} to={item.path} {...props}>{body}</Link>
    );
  };

  return (
    <div className={`app ${area}`}>
      <a className="skip" href="#main">跳到主要内容</a>
      <header className="topbar">
        <button type="button" className="btn icon plain menu-btn" aria-label="打开导航" aria-expanded={open} onClick={() => setOpen(true)}>
          <Menu size={20} />
        </button>
        <Link to="/" className="brand"><span className="brand-mark" aria-hidden="true">乐</span><span className="brand-name">乐初</span></Link>
        <div className="world" data-world={area} role="group" aria-label="切换运营台与后台设置">
          <span className="world-pill" aria-hidden="true" />
          {(["front", "back"] as Area[]).map((value) => (
            <button key={value} type="button" aria-pressed={area === value} onClick={() => navigate(HOME[value])}>
              {value === "front" ? "运营台" : "后台设置"}
            </button>
          ))}
        </div>
        <form className="top-search" onSubmit={submit} role="search">
          <Search size={16} aria-hidden="true" />
          <input type="search" name="q" autoComplete="off" spellCheck={false} aria-label="搜索买家或会话" placeholder="搜买家、会话内容…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </form>
        <div className="top-right">
          <Link to="/inbox?state=waiting" className="top-alert" aria-label={`${waiting} 个买家等你回复`}>
            <Bell size={18} aria-hidden="true" />
            {waiting > 0 ? <span className="num">{waiting} 个待回复</span> : <span>没有待回复</span>}
          </Link>
        </div>
      </header>

      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
      <aside className="sider" data-open={open} aria-label="主导航">
        <nav className="nav" aria-label={area === "front" ? "运营台入口" : "后台设置入口"}>
          {Object.entries(groups).map(([group, list]) => (
            <div key={group}>
              <div className="nav-group">{group}</div>
              {list.map((item) => entry(item))}
            </div>
          ))}

        </nav>
      </aside>

      <main id="main" className="page" data-page={current?.key}>
        <Outlet />
      </main>
    </div>
  );
}
