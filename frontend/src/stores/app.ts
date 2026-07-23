import { defineStore } from "pinia";
import { ref } from "vue";

export interface AppError {
  id: number;
  message: string;
  source: string;
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

/**
 * 应用级 store：错误总线 + 页面内确认弹窗状态。
 * 各 service 失败时 push 到错误总线，由全局 toast 组件统一展示；破坏性操作通过
 * requestConfirmation 返回 Promise，避免 sandbox iframe 禁止浏览器原生 confirm。
 * currentTaskId / configPersist* 等字段随对应 view 阶段补齐（YAGNI，不预建）。
 */
export const useAppStore = defineStore("app", () => {
  const errors = ref<AppError[]>([]);
  const confirmation = ref<ConfirmationRequest | null>(null);
  let seq = 0;
  let confirmationResolver: ((confirmed: boolean) => void) | null = null;
  // 上限：超过则丢弃最旧，防止轮询失败等高频错误无限堆积。
  const MAX_ERRORS = 20;

  function pushError(message: string, source: string): void {
    // 去重：同 message+source 的错误已存在则刷新 at + message（重置 toast 计时），不新增条目。
    const existing = errors.value.find((e) => e.message === message && e.source === source);
    if (existing) {
      existing.at = Date.now();
      // 触发响应式：重新赋值数组引用
      errors.value = [...errors.value];
      window.setTimeout(() => dismiss(existing.id), 4000);
      return;
    }
    const err: AppError = { id: ++seq, message, source, at: Date.now() };
    errors.value.push(err);
    if (errors.value.length > MAX_ERRORS) {
      errors.value = errors.value.slice(-MAX_ERRORS);
    }
    // 自动过期清理，避免堆积（与 toast 生命周期一致）
    window.setTimeout(() => dismiss(err.id), 4000);
  }

  function dismiss(id: number): void {
    errors.value = errors.value.filter((e) => e.id !== id);
  }

  function clear(): void {
    errors.value = [];
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
    confirmation,
    pushError,
    dismiss,
    clear,
    requestConfirmation,
    resolveConfirmation,
  };
});
