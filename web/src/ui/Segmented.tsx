import { useLayoutEffect, useRef, useState } from "react";

export interface SegOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

// A radio group with a sliding pill. Arrow keys move the selection.
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: SegOption<T>[];
  onChange: (value: T) => void;
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const [pill, setPill] = useState({ x: 0, w: 0, ready: false });

  useLayoutEffect(() => {
    const el = refs.current[options.findIndex((option) => option.value === value)];
    if (el) setPill({ x: el.offsetLeft, w: el.offsetWidth, ready: true });
  }, [value, options]);

  const move = (event: React.KeyboardEvent, index: number) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = options[(index + step + options.length) % options.length];
    onChange(next.value);
    refs.current[options.indexOf(next)]?.focus();
  };

  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      <span className="seg-pill" aria-hidden="true" style={{ transform: `translateX(${pill.x}px)`, width: pill.w, opacity: pill.ready ? 1 : 0 }} />
      {options.map((option, index) => (
        <button
          key={option.value}
          ref={(el) => { refs.current[index] = el; }}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          tabIndex={option.value === value ? 0 : -1}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => move(event, index)}
        >
          {option.label}
          {option.count !== undefined && <span className="count num">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}
