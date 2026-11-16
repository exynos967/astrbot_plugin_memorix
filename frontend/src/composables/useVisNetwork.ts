import { onBeforeUnmount, shallowRef, type InjectionKey } from "vue";
import { Network } from "vis-network";
import { DataSet } from "vis-data";
import { useGraphStore } from "@/stores/graph";
import {
  buildLabelUpdates,
  graphPalette,
  isDarkTheme,
  normalizeGraphEdge,
  normalizeGraphNode,
  prefersReducedMotion,
} from "@/utils/graphText";
import type { GraphEdge, GraphNode } from "@/services/graphApi";

/**
 * vis-network 封装 composable：持有 network + DataSet（shallowRef），
 * 支持亮暗主题实时切换（监听 html[data-theme]）、点击空白画布重置选中与高亮、
 * focusNode 联动同步选中态。
 */
export interface UseVisNetworkOptions {
  onNodeSelect?: (nodeId: string) => void;
  onEdgeSelect?: (edgeId: string) => void;
  onCanvasClear?: () => void;
}

export type VisController = ReturnType<typeof useVisNetwork>;

export const GRAPH_VIS_KEY: InjectionKey<VisController> = Symbol("graph-vis");

const ZOOM_MIN = 0.35;
const ZOOM_MAX = 2.4;
const AUTO_FIT_DELAY = 650;

function clampZoom(scale: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number(scale) || 1));
}

export function useVisNetwork(opts: UseVisNetworkOptions = {}) {
  const store = useGraphStore();
  const network = shallowRef<Network | null>(null);
  const nodes = shallowRef<DataSet<Record<string, unknown>> | null>(null);
  const edges = shallowRef<DataSet<Record<string, unknown>> | null>(null);
  const ready = shallowRef(false);
  const initError = shallowRef("");

  let fitInFlight = false;
  let autoFitTimer: number | null = null;
  let labelRaf: number | null = null;
  let containerEl: HTMLElement | null = null;
  let themeObserver: MutationObserver | null = null;

  function shouldAnimate(animated: boolean): boolean {
    return !!animated && !store.lowPerf && !prefersReducedMotion();
  }

  function palette() {
    return graphPalette(isDarkTheme());
  }

  function clearAutoFit(): void {
    if (autoFitTimer != null) {
      window.clearTimeout(autoFitTimer);
      autoFitTimer = null;
    }
  }

  /** 响应 html[data-theme] 变更，动态重绘节点/边与高亮配色。 */
  function refreshThemeColors(): void {
    const net = network.value;
    const ns = nodes.value;
    const es = edges.value;
    if (!net || !ns || !es) return;
    applyResponsiveOptions();
    if (store.highlightedNode) {
      highlightNeighborhood(store.highlightedNode);
    } else {
      const p = palette();
      if (store.rawNodes.length) {
        ns.update(store.rawNodes.map((n) => normalizeGraphNode(n, p)));
      }
      if (store.rawEdges.length) {
        es.update(store.rawEdges.map((e) => normalizeGraphEdge(e, p)));
      }
      updateLabelsByZoom();
    }
    net.redraw();
  }

  function ensureThemeObserver(): void {
    if (themeObserver || typeof document === "undefined" || typeof MutationObserver === "undefined") {
      return;
    }
    themeObserver = new MutationObserver(() => {
      refreshThemeColors();
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
  }

  /** 创建 network 实例，失败置 initError 且不缓存。 */
  function ensureGraph(container: HTMLElement): boolean {
    if (network.value) return true;
    try {
      const dsNodes = new DataSet<Record<string, unknown>>([]);
      const dsEdges = new DataSet<Record<string, unknown>>([]);
      nodes.value = dsNodes;
      edges.value = dsEdges;
      const net = new Network(
        container,
        { nodes: dsNodes, edges: dsEdges },
        baseOptions() as never,
      );
      net.on("click", (params: { nodes?: unknown[]; edges?: unknown[] }) => {
        if (params.nodes && params.nodes.length) {
          opts.onNodeSelect?.(String(params.nodes[0]));
        } else if (params.edges && params.edges.length) {
          opts.onEdgeSelect?.(String(params.edges[0]));
        } else {
          // 点击空白画布：清空节点/边选中状态并重置邻域高亮
          store.selectedNode = "";
          store.selectedEdgeId = "";
          resetHighlight();
          try {
            net.unselectAll();
          } catch {
            /* ignore */
          }
          opts.onCanvasClear?.();
        }
      });
      net.on("zoom", (params: { scale: number }) => {
        store.zoom = clampZoom(params.scale);
        scheduleUpdateLabels();
      });
      network.value = net;
      ready.value = true;
      initError.value = "";
      ensureThemeObserver();
      return true;
    } catch (err) {
      initError.value = err instanceof Error ? err.message : String(err);
      ready.value = false;
      network.value = null;
      nodes.value = null;
      edges.value = null;
      return false;
    }
  }

  function baseOptions(): Record<string, unknown> {
    const p = palette();
    return {
      autoResize: true,
      interaction: {
        hover: true,
        tooltipDelay: 120,
        multiselect: false,
        zoomView: true,
        zoomSpeed: 0.6,
      },
      physics: {
        enabled: store.simulationRunning,
        solver: "forceAtlas2Based",
        stabilization: { iterations: 160 },
      },
      nodes: {
        shape: "dot",
        size: 16,
        borderWidth: 2,
        color: {
          background: p.nodeBg,
          border: p.nodeBorder,
          highlight: { background: p.highlightFirstBg, border: p.highlightCenterBorder },
        },
        font: { color: p.nodeFont, size: 13, face: p.fontFace },
      },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: 0.55 } },
        color: { color: p.edgeColor, highlight: p.edgeHighlight },
        font: {
          color: p.edgeFont,
          size: 11,
          strokeWidth: 3,
          strokeColor: p.edgeStroke,
          face: p.fontFace,
        },
        smooth: { enabled: true, type: "dynamic" },
      },
    };
  }

  function applyResponsiveOptions(): void {
    const net = network.value;
    if (!net) return;
    const compact = containerEl ? containerEl.clientWidth < 560 : false;
    const p = palette();
    net.setOptions({
      physics: {
        enabled: store.simulationRunning,
        solver: "forceAtlas2Based",
        forceAtlas2Based: {
          springLength: compact ? 86 : 130,
          gravitationalConstant: compact ? -32 : -50,
        },
        stabilization: { iterations: compact ? 220 : 160 },
      },
      nodes: {
        size: compact ? 11 : 16,
        color: {
          background: p.nodeBg,
          border: p.nodeBorder,
          highlight: { background: p.highlightFirstBg, border: p.highlightCenterBorder },
        },
        font: { color: p.nodeFont, size: compact ? 10 : 13, face: p.fontFace },
        shadow: {
          enabled: !store.lowPerf,
          color: p.nodeShadow,
          size: 8,
        },
      },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: compact ? 0.42 : 0.55 } },
        color: { color: p.edgeColor, highlight: p.edgeHighlight },
        font: {
          color: p.edgeFont,
          size: compact ? 9 : 11,
          strokeWidth: 3,
          strokeColor: p.edgeStroke,
          face: p.fontFace,
        },
        smooth: store.lowPerf ? false : { enabled: true, type: "dynamic" },
      },
    } as never);
  }

  function syncNetworkSize(): boolean {
    const net = network.value;
    if (!net || !containerEl) return false;
    const width = containerEl.clientWidth;
    const height = containerEl.clientHeight;
    if (width <= 0 || height <= 0) return false;
    net.setSize(`${width}px`, `${height}px`);
    return true;
  }

  function scheduleUpdateLabels(): void {
    if (labelRaf != null) return;
    labelRaf = window.requestAnimationFrame(() => {
      labelRaf = null;
      updateLabelsByZoom();
    });
  }

  function updateLabelsByZoom(): void {
    const net = network.value;
    const ns = nodes.value;
    if (!net || !ns || store.highlightedNode) return;
    const scale = clampZoom(net.getScale());
    const updates = buildLabelUpdates(store.rawNodes, store.rawEdges, scale);
    if (updates.length) ns.update(updates);
  }

  function fitGraphView(animated = true): void {
    const net = network.value;
    if (!net) return;
    if (fitInFlight) return;
    fitInFlight = true;
    window.requestAnimationFrame(() => {
      try {
        syncNetworkSize();
        net.redraw();
        net.fit({
          animation: shouldAnimate(animated)
            ? { duration: 320, easingFunction: "easeInOutQuad" }
            : false,
        });
        const delay = shouldAnimate(animated) ? 360 : 40;
        window.setTimeout(() => {
          store.zoom = clampZoom(net.getScale());
          fitInFlight = false;
        }, delay);
      } catch {
        fitInFlight = false;
      }
    });
  }

  function resizeGraphView(animated = false): void {
    const net = network.value;
    if (!net) return;
    applyResponsiveOptions();
    window.requestAnimationFrame(() => {
      try {
        syncNetworkSize();
        net.redraw();
        if (!store.userZoomed) fitGraphView(animated);
      } catch {
        /* ignore resize race during route changes */
      }
    });
  }

  function setZoom(scale: number, animated = true): void {
    const net = network.value;
    if (!net) return;
    store.userZoomed = true;
    clearAutoFit();
    const nextScale = clampZoom(scale);
    net.moveTo({
      position: net.getViewPosition(),
      scale: nextScale,
      animation: shouldAnimate(animated)
        ? { duration: 180, easingFunction: "easeInOutQuad" }
        : false,
    });
    store.zoom = nextScale;
  }

  function adjustZoom(delta: number): void {
    setZoom((store.zoom || 1) + delta, true);
  }

  /** 聚焦指定节点并同步更新详情选中态（清空边选中）。 */
  function focusNode(nodeId: string, scale = 1.4): void {
    const net = network.value;
    if (!net || !nodeId) return;
    store.selectedNode = nodeId;
    store.selectedEdgeId = "";
    store.userZoomed = true;
    clearAutoFit();
    net.selectNodes([nodeId]);
    net.focus(nodeId, {
      scale,
      animation: shouldAnimate(true) ? { duration: 220, easingFunction: "easeInOutQuad" } : false,
    });
    store.zoom = scale;
  }

  function selectNode(nodeId: string): void {
    if (!nodeId) return;
    store.selectedNode = nodeId;
    store.selectedEdgeId = "";
    network.value?.selectNodes([nodeId]);
  }

  function renderGraph(container: HTMLElement): void {
    containerEl = container;
    if (!ensureGraph(container)) {
      throw new Error(initError.value || "图谱初始化失败");
    }
    const net = network.value;
    const ns = nodes.value;
    const es = edges.value;
    if (!net || !ns || !es) return;
    try {
      store.userZoomed = false;
      clearAutoFit();
      applyResponsiveOptions();
      syncNetworkSize();
      net.off("stabilized");
      ns.clear();
      es.clear();
      const p = palette();
      const rawNodes: GraphNode[] = store.rawNodes;
      const rawEdges: GraphEdge[] = store.rawEdges;
      ns.add(rawNodes.map((n) => normalizeGraphNode(n, p)));
      es.add(rawEdges.map((e) => normalizeGraphEdge(e, p)));
      updateLabelsByZoom();
      if (store.simulationRunning) {
        net.once("stabilized", () => {
          if (!store.userZoomed) fitGraphView(true);
          saveSnapshot();
        });
      }
      fitGraphView(true);
      autoFitTimer = window.setTimeout(() => {
        autoFitTimer = null;
        if (!store.userZoomed) fitGraphView(false);
        if (!store.simulationRunning) saveSnapshot();
      }, AUTO_FIT_DELAY);
    } catch (err) {
      throw err instanceof Error ? err : new Error(String(err));
    }
  }

  function saveSnapshot(): void {
    const net = network.value;
    if (!net) return;
    const positions: Record<string, { x: number; y: number }> = {};
    store.rawNodes.forEach((n) => {
      try {
        const pos = net.getPositions([n.id])[n.id];
        if (pos) positions[n.id] = { x: pos.x, y: pos.y };
      } catch {
        /* 节点可能未布局，跳过 */
      }
    });
    store.saveLayoutSnapshot(positions);
  }

  function restoreLayout(): void {
    const net = network.value;
    const snapshot = store.layoutSnapshot;
    if (!net || !snapshot || Object.keys(snapshot).length === 0) {
      fitGraphView(true);
      return;
    }
    net.once("beforeDrawing", () => {
      Object.entries(snapshot).forEach(([id, pos]) => {
        try {
          net.moveNode(id, pos.x, pos.y);
        } catch {
          /* ignore */
        }
      });
    });
    net.redraw();
    fitGraphView(true);
  }

  function toggleSimulation(): void {
    const net = network.value;
    if (!net) return;
    store.userZoomed = true;
    clearAutoFit();
    store.simulationRunning = !store.simulationRunning;
    net.setOptions({ physics: { enabled: store.simulationRunning } } as never);
    if (store.simulationRunning) {
      net.stabilize(140);
    }
  }

  function applyLowPerf(force?: boolean): void {
    store.lowPerf = typeof force === "boolean" ? force : !store.lowPerf;
    applyResponsiveOptions();
  }

  /** 亮暗双主题自适应邻域高亮（读取 html[data-theme="dark"] 调色板）。 */
  function highlightNeighborhood(nodeId: string): void {
    const net = network.value;
    const ns = nodes.value;
    const es = edges.value;
    if (!net || !ns || !es || !nodeId) return;
    const p = palette();
    const first = new Set<string>((net.getConnectedNodes(nodeId) as string[]) || []);
    const second = new Set<string>();
    first.forEach((id) => {
      ((net.getConnectedNodes(id) as string[]) || []).forEach((next) => second.add(next));
    });
    const nodeUpdates = store.rawNodes.map((node) => {
      const base = normalizeGraphNode(node, p) as Record<string, unknown>;
      const isCenter = node.id === nodeId;
      const isFirst = first.has(node.id);
      const isSecond = second.has(node.id);
      if (isCenter) {
        return {
          ...base,
          label: node.label || node.id,
          size: 23,
          color: { background: p.highlightCenterBg, border: p.highlightCenterBorder },
          font: { color: p.highlightCenterFont, size: 14, face: p.fontFace },
        };
      }
      if (isFirst) {
        return {
          ...base,
          label: node.label || node.id,
          color: { background: p.highlightFirstBg, border: p.highlightFirstBorder },
          font: { color: p.highlightFirstFont, size: 13, face: p.fontFace },
        };
      }
      if (isSecond) {
        return {
          ...base,
          color: { background: p.highlightSecondBg, border: p.highlightSecondBorder },
          font: { color: p.highlightSecondFont, size: 11, face: p.fontFace },
        };
      }
      return {
        ...base,
        label: " ",
        color: { background: p.dimNodeBg, border: p.dimNodeBorder },
        font: { color: p.dimNodeFont, size: 10, face: p.fontFace },
      };
    });
    const edgeUpdates = store.rawEdges.map((edge) => {
      const base = normalizeGraphEdge(edge, p) as Record<string, unknown>;
      const touchesCenter = edge.from === nodeId || edge.to === nodeId;
      const withinFirst = first.has(edge.from) && first.has(edge.to);
      if (touchesCenter) {
        return {
          ...base,
          color: { color: p.highlightCenterBg, highlight: p.edgeHighlight },
          width: 3,
          font: {
            color: p.highlightFirstFont,
            size: 11,
            strokeWidth: 3,
            strokeColor: p.edgeStroke,
            face: p.fontFace,
          },
        };
      }
      if (withinFirst) {
        return {
          ...base,
          color: { color: p.highlightFirstBg, highlight: p.edgeHighlight },
          width: 1.8,
          font: {
            color: p.edgeFont,
            size: 10,
            strokeWidth: 3,
            strokeColor: p.edgeStroke,
            face: p.fontFace,
          },
        };
      }
      return {
        ...base,
        color: { color: p.dimEdgeColor },
        font: {
          color: p.dimEdgeFont,
          size: 10,
          strokeWidth: 3,
          strokeColor: p.edgeStroke,
          face: p.fontFace,
        },
      };
    });
    store.highlightedNode = nodeId;
    ns.update(nodeUpdates);
    es.update(edgeUpdates);
    net.selectNodes([nodeId]);
  }

  function resetHighlight(): void {
    const ns = nodes.value;
    const es = edges.value;
    store.highlightedNode = "";
    if (!ns || !es) return;
    const p = palette();
    ns.update(store.rawNodes.map((n) => normalizeGraphNode(n, p)));
    es.update(store.rawEdges.map((e) => normalizeGraphEdge(e, p)));
    updateLabelsByZoom();
  }

  function retryInit(container: HTMLElement): void {
    destroy();
    renderGraph(container);
  }

  function destroy(): void {
    clearAutoFit();
    if (themeObserver) {
      themeObserver.disconnect();
      themeObserver = null;
    }
    if (labelRaf != null) {
      window.cancelAnimationFrame(labelRaf);
      labelRaf = null;
    }
    const net = network.value;
    if (net) {
      try {
        net.off("click");
        net.off("zoom");
        net.off("stabilized");
        net.destroy();
      } catch {
        /* ignore */
      }
    }
    network.value = null;
    nodes.value = null;
    edges.value = null;
    ready.value = false;
    fitInFlight = false;
  }

  onBeforeUnmount(() => {
    destroy();
  });

  return {
    network,
    ready,
    initError,
    ensureGraph,
    renderGraph,
    retryInit,
    fitGraphView,
    resizeGraphView,
    setZoom,
    adjustZoom,
    focusNode,
    selectNode,
    toggleSimulation,
    applyLowPerf,
    highlightNeighborhood,
    resetHighlight,
    restoreLayout,
    saveSnapshot,
    destroy,
  };
}
