import { defineStore } from "pinia";
import { ref } from "vue";

export type LogLevel = "info" | "success" | "warn" | "error";

export interface LogEntry {
  id: number;
  time: string;
  message: string;
  level: LogLevel;
  source?: string;
}

const MAX_ENTRIES = 300;

function nowTime(): string {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

/**
 * 活动日志 store：各 service/view 与全局错误总线统一写入，LogsView 支持多级筛选与全文检索。
 * 新条目 prepend，保留近 MAX_ENTRIES 条。
 */
export const useLogsStore = defineStore("logs", () => {
  const entries = ref<LogEntry[]>([]);
  let seq = 0;

  function log(message: string, level: LogLevel = "info", source?: string): void {
    const entry: LogEntry = {
      id: ++seq,
      time: nowTime(),
      message,
      level,
      ...(source ? { source } : {}),
    };
    entries.value = [entry, ...entries.value].slice(0, MAX_ENTRIES);
  }

  function clear(): void {
    entries.value = [];
  }

  return { entries, log, clear };
});
