// Dashboard 文本/状态派生工具：从 legacy index.html 状态映射函数忠实移植。
//   statusLabels/statusLabel    → 行 2798-2811
//   statusTone                  → 行 2813-2819
//   statusWithBusy              → 行 2821-2824
//   runtimeLabel                → 行 3162-3167
//   runtimeMessageText          → 行 2826-2840
// 纯函数，供 ServiceList / RuntimeBanner 等 presentational 组件复用（DRY）。

import type { RuntimeReport } from "@/services/configApi";

const statusLabels: Record<string, string> = {
  ready: "准备就绪",
  waiting: "等待中",
  running: "运行中",
  queued: "等待中",
  succeeded: "已完成",
  failed: "失败",
  canceled: "已取消",
  unknown: "未知",
};

export function statusLabel(status: string | undefined | null): string {
  const key = String(status || "unknown");
  return statusLabels[key] || key;
}

export type StatusTone = "ready" | "waiting" | "failed" | "canceled" | "unknown";

export function statusTone(status: string | undefined | null): StatusTone {
  const value = String(status || "unknown");
  if (["waiting", "running", "queued"].includes(value)) return "waiting";
  if (["failed", "canceled"].includes(value)) return value as StatusTone;
  if (value === "ready" || value === "succeeded") return "ready";
  return "unknown";
}

/** 服务繁忙时强制显示 running（与 legacy statusWithBusy 一致）。 */
export function statusWithBusy(
  busy: Record<string, boolean>,
  key: string,
  fallback?: string,
): string {
  if (busy[key]) return "running";
  return fallback || "ready";
}

export type RuntimeTone = "ok" | "warn" | "bad";

export function runtimeLabel(report: RuntimeReport | null | undefined): [string, RuntimeTone] {
  if (!report) return ["unknown", "warn"];
  if (report.ok) return ["ok", "ok"];
  if (report.code === "runtime_components_missing") return ["missing", "warn"];
  return ["error", "bad"];
}

export function runtimeMessageText(
  reportOrMessage: RuntimeReport | string | null | undefined,
  fallback = "等待自检",
): string {
  const raw =
    typeof reportOrMessage === "object" ? reportOrMessage?.message : reportOrMessage;
  const text = String(raw ?? "").trim();
  if (!text) return fallback;
  if (text === "embedding runtime self-check passed") return "Embedding 运行时自检通过";
  if (text === "plugin/config unavailable") return "插件或配置不可用";
  if (text.startsWith("embedding probe failed:")) {
    return `Embedding 探测失败：${text.slice("embedding probe failed:".length).trim()}`;
  }
  if (text === "vector_store 或 embedding_manager 未初始化") return text;
  if (text === "无法确定期望 embedding 维度") return text;
  return text;
}
