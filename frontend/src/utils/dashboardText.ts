// Dashboard 文本/状态派生工具：纯函数，供总览、服务列表、运行时横幅与设置页自检复用（DRY）。

import type { RuntimeReport } from "@/services/configApi";
import type { VectorPoolsInfo } from "@/services/memoryAdminApi";

const statusLabels: Record<string, string> = {
  ready: "准备就绪",
  waiting: "等待中",
  running: "运行中",
  queued: "排队中",
  succeeded: "已完成",
  completed: "已完成",
  failed: "异常",
  canceled: "已取消",
  disabled: "已禁用",
  degraded: "降级运行",
  unknown: "待检查",
};

export function statusLabel(status: string | undefined | null): string {
  const key = String(status || "unknown");
  return statusLabels[key] || key;
}

export type StatusTone = "ready" | "waiting" | "failed" | "canceled" | "unknown";

export function statusTone(status: string | undefined | null): StatusTone {
  const value = String(status || "unknown");
  if (["waiting", "running", "queued", "degraded"].includes(value)) return "waiting";
  if (["failed", "canceled"].includes(value)) return value as StatusTone;
  if (value === "ready" || value === "succeeded" || value === "completed") return "ready";
  return "unknown";
}

/** 服务繁忙时强制显示 running。 */
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
  if (!report) return ["待自检", "warn"];
  if (report.ok) return ["健康", "ok"];
  if (report.code === "runtime_components_missing") return ["组件缺失", "warn"];
  return ["异常", "bad"];
}

export function runtimeMessageText(
  reportOrMessage: RuntimeReport | string | null | undefined,
  fallback = "等待运行时自检",
): string {
  const raw =
    typeof reportOrMessage === "object" ? reportOrMessage?.message : reportOrMessage;
  const text = String(raw ?? "").trim();
  if (!text) return fallback;
  if (text === "embedding runtime self-check passed") return "Embedding 运行时自检通过";
  if (text === "plugin/config unavailable") return "插件或配置暂不可用";
  if (text.startsWith("embedding probe failed:")) {
    return `Embedding 探测失败：${text.slice("embedding probe failed:".length).trim()}`;
  }
  if (text === "vector_store 或 embedding_manager 未初始化") return text;
  if (text === "无法确定期望 embedding 维度") return text;
  return text;
}

/** 提取运行时报告中的双池向量信息。 */
export function extractVectorPools(
  report: RuntimeReport | null | undefined,
): VectorPoolsInfo | null {
  if (!report || typeof report !== "object") return null;
  const pools = (report as Record<string, unknown>).vector_pools;
  if (!pools || typeof pools !== "object") return null;
  return pools as VectorPoolsInfo;
}

/** 计算有效向量总量及副标题（兼容 v2.0 双池与传统单池）。 */
export function deriveVectorSummary(
  legacyVectors: number | null | undefined,
  legacyDimension: number | null | undefined,
  report: RuntimeReport | null | undefined,
): {
  total: number | null;
  isDual: boolean;
  paragraphVectors: number;
  graphVectors: number;
  dimension: number | null;
  subtitle: string;
} {
  const pools = extractVectorPools(report);
  const detectedDim =
    Number(
      (report as Record<string, unknown> | null)?.detected_dimension ||
        (report as Record<string, unknown> | null)?.vector_store_dimension ||
        report?.dimension ||
        legacyDimension ||
        0,
    ) || null;

  if (pools && pools.effective_mode === "dual") {
    const pCount = Number(pools.paragraph_pool?.num_vectors ?? 0);
    const gCount = Number(pools.graph_pool?.num_vectors ?? 0);
    const dim =
      Number(pools.paragraph_pool?.dimension || pools.graph_pool?.dimension || 0) ||
      detectedDim;
    return {
      total: pCount + gCount,
      isDual: true,
      paragraphVectors: pCount,
      graphVectors: gCount,
      dimension: dim,
      subtitle: `双池就绪 · 段落池 ${pCount} / 图谱池 ${gCount}`,
    };
  }

  const singleTotal =
    pools?.single_pool?.num_vectors != null
      ? Number(pools.single_pool.num_vectors)
      : legacyVectors != null
        ? Number(legacyVectors)
        : null;
  const dimText = detectedDim ? ` · ${detectedDim}d` : "";
  return {
    total: singleTotal,
    isDual: false,
    paragraphVectors: 0,
    graphVectors: 0,
    dimension: detectedDim,
    subtitle: pools
      ? `单池模式${dimText}`
      : legacyVectors != null
        ? `向量库状态${dimText}`
        : "等待加载向量统计",
  };
}
