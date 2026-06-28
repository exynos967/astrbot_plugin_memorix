import { defineStore } from "pinia";
import { ref } from "vue";

export interface AppError {
  id: number;
  message: string;
  source: string;
  at: number;
}

/**
 * 应用级 store：错误总线 + 跨 view 临时状态。
 * P0 最小骨架：错误总线（push/clear/dismiss）。修复 legacy 多处 catch 静默吞错——
 * 各 service 失败时 push 到此总线，由全局 toast 组件统一展示。
 * currentTaskId / configPersist* 等字段随对应 view 阶段补齐（YAGNI，不预建）。
 */
export const useAppStore = defineStore("app", () => {
  const errors = ref<AppError[]>([]);
  let seq = 0;

  function pushError(message: string, source: string): void {
    const err: AppError = { id: ++seq, message, source, at: Date.now() };
    errors.value.push(err);
    // 自动过期清理，避免堆积（与 toast 生命周期一致）
    window.setTimeout(() => dismiss(err.id), 4000);
  }

  function dismiss(id: number): void {
    errors.value = errors.value.filter((e) => e.id !== id);
  }

  function clear(): void {
    errors.value = [];
  }

  return { errors, pushError, dismiss, clear };
});
