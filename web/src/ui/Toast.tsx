import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Kind = "ok" | "error";
const ToastContext = createContext<(message: string, kind?: Kind) => void>(() => undefined);
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; message: string; kind: Kind }[]>([]);
  const push = useCallback((message: string, kind: Kind = "ok") => {
    const id = Date.now() + Math.random();
    setItems((list) => [...list.slice(-2), { id, message, kind }]);
    window.setTimeout(() => setItems((list) => list.filter((item) => item.id !== id)), kind === "error" ? 6000 : 2600);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((item) => <div key={item.id} className={`toast${item.kind === "error" ? " error" : ""}`}>{item.message}</div>)}
      </div>
    </ToastContext.Provider>
  );
}
