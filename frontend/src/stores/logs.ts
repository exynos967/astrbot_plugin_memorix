import { defineStore } from "pinia";
import { ref } from "vue";

export type LogLevel = "info" | "warn" | "error";

export interface LogEntry {
  id: number;
  time: string;
  message: string;
  level: LogLevel;
}

const MAX_ENTRIES = 200;

function nowTime(): string {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

/**
 * 活动日志 store：纯前端，对应 legacy log() 函数（index.html 行 2592-2599）。
 * 各 service/view 调 log() 记录活动；LogsView 渲染列表。
 * 新条目 prepend，保留近 MAX_ENTRIES 条。
 */
export const useLogsStore = defineStore("logs", () => {
  const entries = ref<LogEntry[]>([]);
  let seq = 0;

  function log(message: string, level: LogLevel = "info"): void {
    const entry: LogEntry = { id: ++seq, time: nowTime(), message, level };
    entries.value = [entry, ...entries.value].slice(0, MAX_ENTRIES);
  }

  function clear(): void {
    entries.value = [];
  }

  return { entries, log, clear };
});
