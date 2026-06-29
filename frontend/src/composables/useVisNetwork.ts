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
 * vis-network 封装 composable：持有 network + DataSet（shallowRef，不 reactive 包装，
 * vis 无响应式原生支持，包装触发无限更新），暴露渲染/缩放/simulation/高亮/布局方法。
 *
 * 集中修复（与计划 Bug 映射表一致）：
 * - C1 loadGraph 白屏：renderGraph try/catch，失败抛出由 store 捕获 + 空状态降级。
 * - C3 once("stabilized") 泄漏：每次 renderGraph 前 off("stabilized")；onBeforeUnmount off 全部事件 + destroy。
 * - C4 simulation 无快照：renderGraph fit 后存节点坐标快照，restoreLayout 回滚。
 * - C6 ensureGraph 静默：initError ref + retryInit，失败不缓存 network。
 * - H1 双 fit 竞态：fitInFlight 互斥，进行中的 fit 完成前不启动新 fit。
 * - H2 autoFitTimer 未清：clearAutoFit 在 setZoom/focus/renderGraph 调用；onBeforeUnmount 清理。
 * - H3 暂停时 stabilize 无效：simulationRunning=false 时跳过 stabilize 直接 fit。
 * - updateLabelsByZoom 性能：RAF 节流合并多次缩放的标签更新。
 *
 * store 只持业务状态，network/DataSet 留本 composable。
 */
export interface UseVisNetworkOptions {
  onNodeSelect?: (nodeId: string) => void;
  onEdgeSelect?: (edgeId: string) => void;
}

/** useVisNetwork 返回类型（供子组件 inject 访问 vis 方法）。 */
export type VisController = ReturnType<typeof useVisNetwork>;

/** provide/inject key：GraphView 提供唯一 vis 控制器，子组件注入访问 zoom/focus/highlight 等。 */
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

  let fitInFlight = false; // H1 互斥
  let autoFitTimer: number | null = null; // H2
  let labelRaf: number | null = null; // updateLabelsByZoom RAF 节流
  let containerEl: HTMLElement | null = null; // 渲染容器引用，供 applyResponsiveOptions 读宽度

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

  /** C6：创建 network 实例，失败置 initError 且不缓存。 */
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
        }
      });
      net.on("zoom", (params: { scale: number }) => {
        store.zoom = clampZoom(params.scale);
        scheduleUpdateLabels();
      });
      network.value = net;
      ready.value = true;
      initError.value = "";
      return true;
    } catch (err) {
      initError.value = err instanceof Error ? err.message : String(err);
      ready.value = false;
      // 失败不缓存：清空引用，retryInit 可重建
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
      interaction: { hover: true, tooltipDelay: 120, multiselect: false, zoomView: true, zoomSpeed: 0.6 },
      physics: {
        enabled: store.simulationRunning,
        solver: "forceAtlas2Based",
        stabilization: { iterations: 160 },
      },
      nodes: {
        shape: "dot",
        size: 16,
        borderWidth: 2,
        color: { background: "#1da7e7", border: "#ffffff", highlight: { background: "#30c5c8", border: "#1f2c3f" } },
        font: { color: p.nodeFont, size: 13, face: "Segoe UI" },
      },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: 0.55 } },
        color: { color: p.edgeColor, highlight: p.edgeHighlight },
        font: { color: p.edgeFont, size: 11, strokeWidth: 3, strokeColor: p.edgeStroke },
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
        font: { color: p.nodeFont, size: compact ? 10 : 13, face: "Segoe UI" },
        shadow: { enabled: !store.lowPerf, color: "rgba(15, 39, 64, 0.15)", size: 8 },
      },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: compact ? 0.42 : 0.55 } },
        color: { color: p.edgeColor, highlight: p.edgeHighlight },
        font: { color: p.edgeFont, size: compact ? 9 : 11, strokeWidth: 3, strokeColor: p.edgeStroke },
        smooth: store.lowPerf ? false : { enabled: true, type: "dynamic" },
      },
    } as never);
  }

  /** RAF 节流标签更新（修 updateLabelsByZoom 性能）。 */
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
    // H1 互斥：进行中的 fit 完成前不启动新 fit
    if (fitInFlight) return;
    fitInFlight = true;
    window.requestAnimationFrame(() => {
      try {
        net.redraw();
        net.fit({ animation: shouldAnimate(animated) ? { duration: 320, easingFunction: "easeInOutQuad" } : false });
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

  function setZoom(scale: number, animated = true): void {
    const net = network.value;
    if (!net) return;
    store.userZoomed = true;
    clearAutoFit();
    const nextScale = clampZoom(scale);
    net.moveTo({
      position: net.getViewPosition(),
      scale: nextScale,
      animation: shouldAnimate(animated) ? { duration: 180, easingFunction: "easeInOutQuad" } : false,
    });
    store.zoom = nextScale;
  }

  function adjustZoom(delta: number): void {
    setZoom((store.zoom || 1) + delta, true);
  }

  function focusNode(nodeId: string, scale = 1.4): void {
    const net = network.value;
    if (!net || !nodeId) return;
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
    network.value?.selectNodes([nodeId]);
  }

  /**
   * 渲染图谱（C1/C3/H2/H3/C4）。
   * 流程：ensureGraph → applyResponsiveOptions → clear/add → updateLabels → fit + stabilized once + autoFit → 存快照。
   * 由 GraphView 在 store.loadGraph 成功后调用，传入容器。
   */
  function renderGraph(container: HTMLElement): void {
    containerEl = container;
    if (!ensureGraph(container)) return;
    const net = network.value;
    const ns = nodes.value;
    const es = edges.value;
    if (!net || !ns || !es) return;
    try {
      store.userZoomed = false;
      clearAutoFit();
      applyResponsiveOptions();
      // C3：每次渲染前 off stabilized，避免旧 handler 泄漏
      net.off("stabilized");
      ns.clear();
      es.clear();
      const p = palette();
      const rawNodes: GraphNode[] = store.rawNodes;
      const rawEdges: GraphEdge[] = store.rawEdges;
      ns.add(rawNodes.map((n) => normalizeGraphNode(n, p)));
      es.add(rawEdges.map((e) => normalizeGraphEdge(e, p)));
      updateLabelsByZoom();
      // H3：暂停时跳过 stabilize 直接 fit；运行时等 stabilized 再 fit
      if (store.simulationRunning) {
        net.once("stabilized", () => {
          if (!store.userZoomed) fitGraphView(true);
        });
      }
      fitGraphView(true);
      // H2：autoFit 定时器，650ms 后若用户未手动缩放再 fit 一次（纠正 stabilize 后位移）
      autoFitTimer = window.setTimeout(() => {
        autoFitTimer = null;
        if (!store.userZoomed) fitGraphView(false);
      }, AUTO_FIT_DELAY);
      // C4：fit 后存坐标快照
      saveSnapshot();
    } catch (err) {
      // C1：渲染异常向上抛，由 GraphView 捕获进 store.initError
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

  /** C4：从快照恢复节点坐标。 */
  function restoreLayout(): void {
    const net = network.value;
    const snapshot = store.layoutSnapshot;
    if (!net || !snapshot || Object.keys(snapshot).length === 0) return;
    net.once("beforeDrawing", () => {
      // vis-network 无批量 move，逐个 moveNode（节点数有限可接受）
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
          color: { background: "#0f5fc8", border: "#ffffff" },
          font: { color: "#0f1111", size: 14, face: "Segoe UI" },
        };
      }
      if (isFirst) {
        return {
          ...base,
          label: node.label || node.id,
          color: { background: "#54b7ff", border: "#ffffff" },
          font: { color: "#1b2736", size: 13, face: "Segoe UI" },
        };
      }
      if (isSecond) {
        return {
          ...base,
          color: { background: "#dcecff", border: "#ffffff" },
          font: { color: "#73859a", size: 11, face: "Segoe UI" },
        };
      }
      return {
        ...base,
        label: " ",
        color: { background: "#eef2f4", border: "#ffffff" },
        font: { color: "#b5bec7", size: 10, face: "Segoe UI" },
      };
    });
    const edgeUpdates = store.rawEdges.map((edge) => {
      const base = normalizeGraphEdge(edge, p) as Record<string, unknown>;
      const touchesCenter = edge.from === nodeId || edge.to === nodeId;
      const withinFirst = first.has(edge.from) && first.has(edge.to);
      if (touchesCenter) return { ...base, color: { color: "#0f5fc8", highlight: "#0f5fc8" }, width: 3 };
      if (withinFirst) return { ...base, color: { color: "#77bdf6", highlight: "#2388ff" }, width: 1.8 };
      return {
        ...base,
        color: { color: "rgba(190, 201, 212, 0.35)" },
        font: { color: "#c5cdd5", size: 10, strokeWidth: 3, strokeColor: "#ffffff" },
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

  /** C6：重试初始化。 */
  function retryInit(container: HTMLElement): void {
    destroy();
    renderGraph(container);
  }

  /** C3：清理所有事件 + 销毁 network。 */
  function destroy(): void {
    clearAutoFit();
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
