"use client";

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { CheckCircle2, AlertTriangle, X } from "lucide-react";

type ToastKind = "success" | "error";
interface Toast {
  id: number;
  kind: ToastKind;
  msg: string;
}

const Ctx = createContext<{
  toast: (msg: string, kind?: ToastKind) => void;
}>({ toast: () => {} });

export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback((msg: string, kind: ToastKind = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, msg }]);
    setTimeout(
      () => setToasts((t) => t.filter((x) => x.id !== id)),
      kind === "error" ? 6000 : 3500
    );
  }, []);

  return (
    <Ctx.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-5 right-5 z-[100] flex flex-col gap-2 w-[360px] max-w-[calc(100vw-2.5rem)]">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="animate-fade-in card shadow-pop px-4 py-3 flex items-start gap-3"
          >
            {t.kind === "success" ? (
              <CheckCircle2 size={18} className="text-good mt-0.5 shrink-0" />
            ) : (
              <AlertTriangle size={18} className="text-bad mt-0.5 shrink-0" />
            )}
            <p className="text-sm text-ink-soft flex-1 break-words">{t.msg}</p>
            <button
              onClick={() =>
                setToasts((x) => x.filter((y) => y.id !== t.id))
              }
              className="text-ink-faint hover:text-ink"
            >
              <X size={15} />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
