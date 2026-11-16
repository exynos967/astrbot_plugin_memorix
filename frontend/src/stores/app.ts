import { defineStore } from "pinia";
import { ref } from "vue";
import { useLogsStore } from "@/stores/logs";

export type ToastTone = "success" | "info" | "warn" | "error";

export interface AppError {
  id: number;
  message: string;
  source: string;
  at: number;
}

export interface ToastItem {
  id: number;
  message: string;
  source?: string;
  tone: ToastTone;
  at: number;
}

export interface ConfirmationRequest {
  title: string;
  message: string;
  confirmText: string;
  cancelText: string;
  danger: boolean;
}

export type ConfirmationOptions = Pick<ConfirmationRequest, "title" | "message"> &
  Partial<Pick<ConfirmationRequest, "confirmText" | "cancelText" | "danger">>;

const TOAST_TTL_MS: Record<ToastTone, number> = {
  success: 3200,
  info: 3600,
  warn: 4600,
  error: 5200,
};

/**
 * 应用级 store：统一四色通知总线（toasts）+ 兼容旧错误队列（errors）+ 页面内确认弹窗状态。
 * - 使用 Map<number, number> 管理每个通知的定时器，去重刷新或悬停暂停时先 clearTimeout，杜绝早退 bug。
 * - pushError 自动同步写入 useLogsStore 审计日志。
 */
export const useAppStore = defineStore("app", () => {
  const errors = ref<AppError[]>([]);
  const toasts = ref<ToastItem[]>([]);
  const confirmation = ref<ConfirmationRequest | null>(null);
  let seq = 0;
  let confirmationResolver: ((confirmed: boolean) => void) | null = null;

  const MAX_ITEMS = 20;
  const timers = new Map<number, number>();

  function clearTimer(id: number): void {
    const handle = timers.get(id);
    if (handle !== undefined) {
      window.clearTimeout(handle);
      timers.delete(id);
    }
  }

  function scheduleDismiss(id: number, tone: ToastTone): void {
    clearTimer(id);
    const handle = window.setTimeout(() => {
      timers.delete(id);
      dismissToast(id);
    }, TOAST_TTL_MS[tone]);
    timers.set(id, handle);
  }

  function upsertToast(id: number, message: string, tone: ToastTone, source = ""): void {
    const now = Date.now();
    const existing = toasts.value.find(
      (t) => t.id === id || (t.message === message && (t.source || "") === source && t.tone === tone),
    );
    if (existing) {
      existing.at = now;
      existing.message = message;
      toasts.value = [...toasts.value];
      scheduleDismiss(existing.id, tone);
      return;
    }

    const item: ToastItem = {
      id,
      message,
      ...(source ? { source } : {}),
      tone,
      at: now,
    };
    const next = [...toasts.value, item];
    if (next.length > MAX_ITEMS) {
      const evicted = next.splice(0, next.length - MAX_ITEMS);
      for (const old of evicted) clearTimer(old.id);
    }
    toasts.value = next;
    scheduleDismiss(id, tone);
  }

  function notify(message: string, tone: ToastTone = "info", source = ""): void {
    if (tone === "error") {
      pushError(message, source || "系统");
      return;
    }
    const existing = toasts.value.find(
      (t) => t.message === message && (t.source || "") === source && t.tone === tone,
    );
    const id = existing ? existing.id : ++seq;
    upsertToast(id, message, tone, source);

    try {
      const logs = useLogsStore();
      const formatted = source ? `[${source}] ${message}` : message;
      logs.log(formatted, tone === "success" ? "success" : tone === "warn" ? "warn" : "info", source || undefined);
    } catch {
      // Pinia 未挂载边界安全兜底
    }
  }

  function pushSuccess(message: string, source = ""): void {
    notify(message, "success", source);
  }

  function pushError(message: string, source: string): void {
    const cleanSource = source || "错误";
    try {
      const logs = useLogsStore();
      logs.log(`[${cleanSource}] ${message}`, "error", cleanSource);
    } catch {
      // Pinia 未挂载边界安全兜底
    }

    const now = Date.now();
    const existing = errors.value.find((e) => e.message === message && e.source === cleanSource);
    if (existing) {
      existing.at = now;
      errors.value = [...errors.value];
      upsertToast(existing.id, message, "error", cleanSource);
      return;
    }

    const id = ++seq;
    const err: AppError = { id, message, source: cleanSource, at: now };
    const nextErrors = [...errors.value, err];
    if (nextErrors.length > MAX_ITEMS) {
      const evicted = nextErrors.splice(0, nextErrors.length - MAX_ITEMS);
      for (const old of evicted) clearTimer(old.id);
    }
    errors.value = nextErrors;
    upsertToast(id, message, "error", cleanSource);
  }

  function dismissToast(id: number): void {
    clearTimer(id);
    toasts.value = toasts.value.filter((t) => t.id !== id);
    errors.value = errors.value.filter((e) => e.id !== id);
  }

  function dismiss(id: number): void {
    dismissToast(id);
  }

  function pauseToast(id: number): void {
    clearTimer(id);
  }

  function resumeToast(id: number): void {
    const item = toasts.value.find((t) => t.id === id);
    if (item) {
      scheduleDismiss(id, item.tone);
    }
  }

  function clear(): void {
    for (const id of timers.keys()) {
      clearTimer(id);
    }
    errors.value = [];
    toasts.value = [];
  }

  function requestConfirmation(options: ConfirmationOptions): Promise<boolean> {
    if (confirmationResolver) resolveConfirmation(false);

    confirmation.value = {
      title: options.title,
      message: options.message,
      confirmText: options.confirmText || "确认",
      cancelText: options.cancelText || "取消",
      danger: options.danger ?? false,
    };

    return new Promise<boolean>((resolve) => {
      confirmationResolver = resolve;
    });
  }

  function resolveConfirmation(confirmed: boolean): void {
    const resolve = confirmationResolver;
    confirmationResolver = null;
    confirmation.value = null;
    resolve?.(confirmed);
  }

  return {
    errors,
    toasts,
    confirmation,
    notify,
    pushSuccess,
    pushError,
    dismiss,
    dismissToast,
    pauseToast,
    resumeToast,
    clear,
    requestConfirmation,
    resolveConfirmation,
  };
});
