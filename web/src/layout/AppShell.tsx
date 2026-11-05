import { useQuery } from "@tanstack/react-query";
import { Bell, ExternalLink, Menu, Search, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router";
import { get } from "../api";
import { NAV, areaOf, findNav, type Area } from "../nav";
import { PageIntro, PageIntroProvided, PAGE_PURPOSES } from "../ui/PageIntro";
import "./flow.css";

const HOME: Record<Area, string> = { front: "/", back: "/system" };

interface InboxSummary { ready: boolean; waiting_total: number; draft_new: number }

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const area = areaOf(location.pathname);
  const [open, setOpen] = useState(false);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const menu = useRef<HTMLButtonElement>(null);
  const sider = useRef<HTMLElement>(null);
  const [search, setSearch] = useState("");
  const summary = useQuery({ queryKey: ["inbox-summary"], queryFn: () => get<InboxSummary>("/api/v1/inbox/summary"), refetchInterval: 60_000, retry: false });
  const waiting = !summary.isError && summary.data?.ready && Number.isSafeInteger(summary.data.waiting_total) && summary.data.waiting_total >= 0 ? summary.data.waiting_total : null;
  const current = findNav(location.pathname);
  const introKey = /^\/deals\/[^/]+$/.test(location.pathname) ? "deal" : /^\/quotes\/[^/]+$/.test(location.pathname) ? "quote" : current?.key;
  const introPage = introKey && introKey in PAGE_PURPOSES ? introKey as keyof typeof PAGE_PURPOSES : undefined;

  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const change = () => { setCompact(media.matches); setOpen(false); };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  const closeNavigation = () => { setOpen(false); window.requestAnimationFrame(() => menu.current?.focus()); };
  useEffect(() => {
    if (!compact || !open) return;
    sider.current?.querySelector<HTMLAnchorElement>('a[aria-current="page"], a')?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeNavigation(); }
      if (event.key !== "Tab") return;
      const targets = sider.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])');
      if (!targets?.length) return;
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => document.removeEventListener("keydown", keydown);
  }, [compact, open]);

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
        {item.key === "inbox" && waiting != null && waiting > 0 && <span className="badge num" aria-label={`${waiting} 个待回复`}>{waiting}</span>}
        {item.kind === "external" && <ExternalLink size={13} className="ext" aria-label="在原控制台打开" />}
      </>
    );
    return item.kind === "external" ? (
      <a key={item.key} href={item.path} {...props}>{body}</a>
    ) : (
      <Link key={item.key} to={item.path} onClick={() => setOpen(false)} {...props}>{body}</Link>
    );
  };

  return (
    <div className={`app ${area}`}>
      <a className="skip" href="#main">跳到主要内容</a>
      <header className="topbar" inert={compact && open}>
        <button ref={menu} type="button" className="btn icon plain menu-btn" aria-label="打开导航" aria-controls="primary-navigation" aria-expanded={open} onClick={() => setOpen(true)}>
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
          <Link to="/inbox?state=waiting&role=customer" className="top-alert" aria-label={waiting == null ? "待回复数量未知，打开消息查看" : `${waiting} 个客户等你回复`}>
            <Bell size={18} aria-hidden="true" />
            {waiting == null ? <span>待回复 —</span> : waiting > 0 ? <span className="num">{waiting} 个待回复</span> : <span>没有待回复</span>}
          </Link>
        </div>
      </header>

      {open && <div className="scrim" onClick={closeNavigation} aria-hidden="true" />}
      <aside ref={sider} id="primary-navigation" className="sider" data-open={open} aria-label="主导航" inert={compact && !open} aria-hidden={compact && !open ? true : undefined}>
        <button type="button" className="btn plain flow-nav-close" onClick={closeNavigation}><X size={18} aria-hidden="true" />关闭导航</button>
        <nav className="nav" aria-label={area === "front" ? "运营台入口" : "后台设置入口"}>
          {Object.entries(groups).map(([group, list]) => (
            <div key={group}>
              <div className="nav-group">{group}</div>
              {list.map((item) => entry(item))}
            </div>
          ))}

        </nav>
      </aside>

      <main id="main" className="page" data-page={current?.key} inert={compact && open} tabIndex={-1}>
        {introPage && <PageIntro page={introPage} />}
        <PageIntroProvided value={Boolean(introPage)}><Outlet /></PageIntroProvided>
      </main>
    </div>
  );
}
