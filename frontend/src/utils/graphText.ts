// Graph 视图纯函数工具：亮暗双主题技术调色板（Cobalt / Teal / Amber / Slate，零紫红/品红）、节点/边规范化、度数计算、缩放标签更新与作用域标签。

import type { GraphEdge, GraphNode } from "@/services/graphApi";

export const GRAPH_FONT_FACE =
  'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif';

/** vis 调色板（依赖当前主题，由调用方传入 dark 布尔）。 */
export interface GraphPalette {
  dark: boolean;
  fontFace: string;
  nodeBg: string;
  nodeDeletedBg: string;
  nodeFont: string;
  nodeMuted: string;
  nodeBorder: string;
  nodeShadow: string;
  ghostBg: string;
  edgeFont: string;
  edgeStroke: string;
  edgeColor: string;
  edgePinned: string;
  edgeInactive: string;
  edgeHighlight: string;
  highlightCenterBg: string;
  highlightCenterBorder: string;
  highlightCenterFont: string;
  highlightFirstBg: string;
  highlightFirstBorder: string;
  highlightFirstFont: string;
  highlightSecondBg: string;
  highlightSecondBorder: string;
  highlightSecondFont: string;
  dimNodeBg: string;
  dimNodeBorder: string;
  dimNodeFont: string;
  dimEdgeColor: string;
  dimEdgeFont: string;
}

export function graphPalette(dark: boolean): GraphPalette {
  return dark
    ? {
        dark: true,
        fontFace: GRAPH_FONT_FACE,
        nodeBg: "#3b82f6",
        nodeDeletedBg: "#ef4444",
        nodeFont: "#e2e8f0",
        nodeMuted: "#94a3b8",
        nodeBorder: "#1e293b",
        nodeShadow: "rgba(2, 6, 23, 0.45)",
        ghostBg: "#475569",
        edgeFont: "#cbd5e1",
        edgeStroke: "#0b111e",
        edgeColor: "#64748b",
        edgePinned: "#fbbf24",
        edgeInactive: "#475569",
        edgeHighlight: "#2dd4bf",
        highlightCenterBg: "#2563eb",
        highlightCenterBorder: "#93c5fd",
        highlightCenterFont: "#f8fafc",
        highlightFirstBg: "#14b8a6",
        highlightFirstBorder: "#99f6e4",
        highlightFirstFont: "#e2e8f0",
        highlightSecondBg: "#1e3a5f",
        highlightSecondBorder: "#475569",
        highlightSecondFont: "#94a3b8",
        dimNodeBg: "rgba(30, 41, 59, 0.5)",
        dimNodeBorder: "rgba(71, 85, 105, 0.4)",
        dimNodeFont: "#475569",
        dimEdgeColor: "rgba(100, 116, 139, 0.22)",
        dimEdgeFont: "#475569",
      }
    : {
        dark: false,
        fontFace: GRAPH_FONT_FACE,
        nodeBg: "#2563eb",
        nodeDeletedBg: "#dc2626",
        nodeFont: "#162436",
        nodeMuted: "#64748b",
        nodeBorder: "#f8fafc",
        nodeShadow: "rgba(15, 23, 42, 0.12)",
        ghostBg: "#cbd5e1",
        edgeFont: "#475569",
        edgeStroke: "#f8fafc",
        edgeColor: "#94a3b8",
        edgePinned: "#d97706",
        edgeInactive: "#cbd5e1",
        edgeHighlight: "#0d9488",
        highlightCenterBg: "#1d4ed8",
        highlightCenterBorder: "#dbeafe",
        highlightCenterFont: "#0f172a",
        highlightFirstBg: "#0d9488",
        highlightFirstBorder: "#ccfbf1",
        highlightFirstFont: "#162436",
        highlightSecondBg: "#dbeafe",
        highlightSecondBorder: "#93c5fd",
        highlightSecondFont: "#475569",
        dimNodeBg: "#e2e8f0",
        dimNodeBorder: "#f8fafc",
        dimNodeFont: "#94a3b8",
        dimEdgeColor: "rgba(148, 163, 184, 0.34)",
        dimEdgeFont: "#94a3b8",
      };
}

/** vis-network 节点规范化（支持亮暗双主题）。 */
export function normalizeGraphNode(n: GraphNode, palette: GraphPalette): Record<string, unknown> {
  return {
    id: n.id,
    label: n.label || n.id,
    title: n.label && n.label !== n.id ? `${n.label} (${n.id})` : n.id,
    color: n.is_deleted
      ? { background: palette.nodeDeletedBg, border: palette.nodeBorder }
      : n.is_ghost
        ? { background: palette.ghostBg, border: palette.nodeBorder }
        : { background: palette.nodeBg, border: palette.nodeBorder },
    font: { color: n.is_ghost ? palette.nodeMuted : palette.nodeFont, face: palette.fontFace },
  };
}

/** vis-network 边规范化（精确保留原始权重 raw_value，避免 || 1 吞掉 0.05 冷冻边权重；过滤任何后端遗留紫红硬编码）。 */
export function normalizeGraphEdge(e: GraphEdge, palette: GraphPalette): Record<string, unknown> {
  const hasNumericValue = e.value != null && Number.isFinite(Number(e.value));
  const rawValue = hasNumericValue ? Number(e.value) : 1;
  const edgeColor =
    e.is_active === false
      ? { color: palette.edgeInactive }
      : e.is_pinned
        ? { color: palette.edgePinned, highlight: palette.edgeHighlight }
        : { color: palette.edgeColor, highlight: palette.edgeHighlight };

  return {
    id: e.id || `${e.from}_${e.to}`,
    from: e.from,
    to: e.to,
    label: e.label || "",
    value: Math.max(0.5, rawValue),
    raw_value: rawValue,
    arrows: "to",
    dashes: !!e.dashes || e.is_active === false,
    color: edgeColor,
    font: {
      color: palette.edgeFont,
      strokeWidth: 3,
      strokeColor: palette.edgeStroke,
      face: palette.fontFace,
    },
    predicates: e.predicates || [],
    is_pinned: !!e.is_pinned,
    is_protected: !!e.is_protected,
    protected_until: e.protected_until,
  };
}

/** 格式化边权重显示（不把 0.05 覆写为 1）。 */
export function formatEdgeWeight(value: unknown): string {
  if (value == null || value === "") return "1.00";
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(2) : "1.00";
}

/** 计算每个节点的度数。 */
export function graphDegrees(edges: GraphEdge[]): Map<string, number> {
  const degree = new Map<string, number>();
  edges.forEach((edge) => {
    degree.set(edge.from, (degree.get(edge.from) || 0) + 1);
    degree.set(edge.to, (degree.get(edge.to) || 0) + 1);
  });
  return degree;
}

/**
 * 构建按缩放显示/隐藏标签的更新列表。
 * scale >= 0.82 或 hub 节点（度数 >= 前 25% 分位）显示标签，否则隐藏（label=" "）。
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

/** scope 标签："default" 统一映射为「自动 / 最近」。 */
export function scopeLabel(scopeKey: string, options?: { value: string; label?: string }[]): string {
  const key = String(scopeKey || "");
  if (key === "default") return "自动 / 最近";
  const known = (options || []).find((item) => item.value === key);
  if (known?.label) return known.label;
  const parts = key.split(":");
  if (parts.length >= 3 && parts[1] === "group") return `${parts[0]}:${parts.slice(2).join(":")}`;
  return key || "自动 / 最近";
}

/** 当前主题是否为暗色（从 dataset.theme 读取）。 */
export function isDarkTheme(): boolean {
  return typeof document !== "undefined" && document.documentElement.dataset.theme === "dark";
}

/** 是否偏好减少动画。 */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
