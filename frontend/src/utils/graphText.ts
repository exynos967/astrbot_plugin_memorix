// Graph 视图纯函数工具：调色板、节点/边规范化、度数计算、缩放标签更新、scope 标签。
// 从 legacy graphPalette/normalizeGraphNode/normalizeGraphEdge/graphDegrees/updateLabelsByZoom/graphScopeLabel
// （index.html 行 3205-3224, 3744-3805, 3356-3363）提取为纯函数，供 useVisNetwork 与组件复用。
//
// 修复点：updateLabelsByZoom 在 legacy 中每次 zoom 都全量 nodes.update（可能数百次/秒拖慢），
// 新实现由 useVisNetwork 用 RAF 节流调用 buildLabelUpdates，再批量 update。

import type { GraphEdge, GraphNode } from "@/services/graphApi";

/** vis 调色板（依赖当前主题，由调用方传入 dark 布尔）。 */
export interface GraphPalette {
  nodeFont: string;
  nodeMuted: string;
  edgeFont: string;
  edgeStroke: string;
  edgeColor: string;
  edgeHighlight: string;
}

export function graphPalette(dark: boolean): GraphPalette {
  return dark
    ? {
        nodeFont: "#dbeafe",
        nodeMuted: "#8091a7",
        edgeFont: "#9fb3c8",
        edgeStroke: "#0b1220",
        edgeColor: "#64748b",
        edgeHighlight: "#93c5fd",
      }
    : {
        nodeFont: "#1b2736",
        nodeMuted: "#91a0ad",
        edgeFont: "#526679",
        edgeStroke: "#ffffff",
        edgeColor: "#9fb4c4",
        edgeHighlight: "#1da7e7",
      };
}

/** vis-network 节点规范化（与 legacy normalizeGraphNode 一致）。 */
export function normalizeGraphNode(n: GraphNode, palette: GraphPalette): Record<string, unknown> {
  return {
    id: n.id,
    label: n.label || n.id,
    title: n.id,
    color: n.is_deleted
      ? { background: "#d94a4a", border: "#ffffff" }
      : n.is_ghost
        ? { background: "#d7e5ee", border: "#ffffff" }
        : { background: "#1da7e7", border: "#ffffff" },
    font: { color: n.is_ghost ? palette.nodeMuted : palette.nodeFont },
  };
}

/** vis-network 边规范化（与 legacy normalizeGraphEdge 一致）。 */
export function normalizeGraphEdge(e: GraphEdge, palette: GraphPalette): Record<string, unknown> {
  const rawValue = Number(e.value || 0);
  return {
    id: e.id || `${e.from}_${e.to}`,
    from: e.from,
    to: e.to,
    label: e.label || "",
    value: Math.max(1, rawValue || 1),
    raw_value: rawValue,
    arrows: "to",
    dashes: !!e.dashes || e.is_active === false,
    color:
      e.color ||
      (e.is_active === false
        ? { color: "#c4ced8" }
        : { color: palette.edgeColor, highlight: palette.edgeHighlight }),
    font: { color: palette.edgeFont, strokeWidth: 3, strokeColor: palette.edgeStroke },
    predicates: e.predicates || [],
    is_pinned: !!e.is_pinned,
    is_protected: !!e.is_protected,
    protected_until: e.protected_until,
  };
}

/** 计算每个节点的度数（与 legacy graphDegrees 一致）。 */
export function graphDegrees(edges: GraphEdge[]): Map<string, number> {
  const degree = new Map<string, number>();
  edges.forEach((edge) => {
    degree.set(edge.from, (degree.get(edge.from) || 0) + 1);
    degree.set(edge.to, (degree.get(edge.to) || 0) + 1);
  });
  return degree;
}

/**
 * 构建按缩放显示/隐藏标签的更新列表（与 legacy updateLabelsByZoom 一致）。
 * scale >= 0.82 或 hub 节点（度数 >= 前 25% 分位）显示标签，否则隐藏（label=" "）。
 * 返回 {id,label} 数组，供 DataSet.update 批量应用。
 */
export function buildLabelUpdates(
  nodes: GraphNode[],
  edges: GraphEdge[],
  scale: number,
): { id: string; label: string }[] {
  const degree = graphDegrees(edges);
  const sorted = Array.from(degree.values()).sort((a, b) => b - a);
  const cutoff = sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(sorted.length * 0.25)))] || 2;
  return nodes.map((node) => {
    const label = node.label || node.id;
    const isHub = (degree.get(node.id) || 0) >= cutoff;
    return { id: node.id, label: scale >= 0.82 || isHub ? label : " " };
  });
}

/** scope 标签（与 legacy graphScopeLabel 一致）。 */
export function scopeLabel(scopeKey: string, options?: { value: string; label?: string }[]): string {
  const key = String(scopeKey || "");
  const known = (options || []).find((item) => item.value === key);
  if (known?.label) return known.label;
  const parts = key.split(":");
  if (parts.length >= 3 && parts[1] === "group") return `${parts[0]}:${parts.slice(2).join(":")}`;
  return key || "自动 / 最近";
}

/** 当前主题是否为暗色（从 dataset.theme 读取，供 palette 使用）。 */
export function isDarkTheme(): boolean {
  return typeof document !== "undefined" && document.documentElement.dataset.theme === "dark";
}

/** 是否偏好减少动画（与 legacy prefersReducedMotion 一致）。 */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
