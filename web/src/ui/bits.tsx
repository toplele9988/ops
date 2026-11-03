import { ChevronDown, Search } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { Tone } from "../labels";

export function Chip({ tone = "plain", dot = true, children }: { tone?: Tone; dot?: boolean; children: ReactNode }) {
  return <span className={`chip ${tone}${dot ? "" : " plain"}`}>{children}</span>;
}

export function SearchField({ value, onSearch, label, placeholder }: {
  value: string;
  onSearch: (value: string) => void;
  label: string;
  placeholder: string;
}) {
  const [text, setText] = useState(value);
  const timer = useRef<number>(undefined);
  useEffect(() => setText(value), [value]);
  const change = (next: string) => {
    setText(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onSearch(next.trim()), 280);
  };
  return (
    <label className="field">
      <span className="sr-only">{label}</span>
      <Search size={16} aria-hidden="true" />
      <input type="search" name="q" autoComplete="off" spellCheck={false} placeholder={placeholder} value={text} onChange={(e) => change(e.target.value)} />
    </label>
  );
}

export function Pick({ value, onChange, options, label, pill }: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  label: string;
  pill?: boolean;
}) {
  return (
    <label className={`select${pill ? " pill" : ""}`}>
      <span className="sr-only">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      <ChevronDown size={14} aria-hidden="true" />
    </label>
  );
}

export function Meter({ rate }: { rate: number }) {
  return (
    <div className={`meter${rate < 0.25 ? " low" : ""}`} role="img" aria-label={`覆盖率 ${Math.round(rate * 100)}%`}>
      <i style={{ width: `${Math.max(2, Math.round(rate * 100))}%` }} />
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="group" aria-busy="true" aria-label="加载中…">
      {Array.from({ length: lines }, (_, i) => (
        <div className="row" key={i}>
          <div className="row-main" style={{ display: "grid", gap: 8 }}>
            <div className="skeleton" style={{ height: 14, width: `${60 + ((i * 17) % 30)}%` }} />
            <div className="skeleton" style={{ height: 12, width: "40%" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Row that expands in place; keeps its place in the list so nothing jumps.
 *  `aside` sits beside the toggle (not inside it) so controls there stay valid, separately focusable elements. */
export function Disclosure({ head, aside, children, defaultOpen = false }: {
  head: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="disc" data-open={open}>
      <div className="disc-head">
        <button type="button" className="disc-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          <ChevronDown size={16} aria-hidden="true" className="chev" />
          <span style={{ flex: 1, minWidth: 0 }}>{head}</span>
        </button>
        {aside && <div className="disc-aside">{aside}</div>}
      </div>
      <div className="disc-body">
        <div className="disc-inner" {...({ inert: !open } as object)}>
          <div className="disc-pad">{children}</div>
        </div>
      </div>
    </div>
  );
}

/** Native <dialog>: focus trap, Escape to close and the backdrop come from the browser. */
export function Sheet({ open, title, onClose, children, wide }: { open: boolean; title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog ref={ref} className={`sheet${wide ? " wide" : ""}`} aria-labelledby={titleId} onClose={onClose} onClick={(e) => e.target === ref.current && onClose()}>
      <div className="sheet-body">
        <h2 id={titleId} className="t-title" style={{ marginBottom: 12 }}>{title}</h2>
        {open && children}
      </div>
    </dialog>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch-track" aria-hidden="true"><i /></span>
      <span className="sr-only">{label}</span>
    </label>
  );
}
