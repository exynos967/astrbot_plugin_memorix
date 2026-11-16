import { defineStore } from "pinia";
import { ref, watch } from "vue";
import type { ScopeOption } from "@/services/configApi";
import {
  createEdge as createEdgeApi,
  createNode as createNodeApi,
  deleteEdge as deleteEdgeApi,
  deleteNode as deleteNodeApi,
  fetchGraph,
  renameNode as renameNodeApi,
  updateEdgeWeight as updateEdgeWeightApi,
  type GraphEdge,
  type GraphNode,
} from "@/services/graphApi";
import { fetchScopes } from "@/services/configApi";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";

/**
 * 图谱 store：全局唯一作用域源（currentScope / effectiveScope）+ 图谱业务状态 + CRUD actions。
 */
export const useGraphStore = defineStore("graph", () => {
  // ===== scope =====
  const currentScope = ref("");
  const resolvedScope = ref("");
  const scopeOptions = ref<ScopeOption[]>([]);

  // ===== 图谱业务状态 =====
  const rawNodes = ref<GraphNode[]>([]);
  const rawEdges = ref<GraphEdge[]>([]);
  const nodeLabels = ref<string[]>([]);
  const predicateLabels = ref<string[]>([]);
  const sourceFocus = ref("");
  const density = ref(0.82);
  const excludeLeaf = ref(true);

  const loading = ref(false);
  const initError = ref("");

  // 交互状态（与 useVisNetwork 双向同步）
  const zoom = ref(1);
  const userZoomed = ref(false);
  const simulationRunning = ref(true);
  const lowPerf = ref(false);
  const highlightedNode = ref("");
  const selectedNode = ref("");
  const selectedEdgeId = ref("");

  /** 布局快照（loadGraph 后存节点坐标，restoreLayout 时回滚）。 */
  const layoutSnapshot = ref<Record<string, { x: number; y: number }>>({});

  const app = useAppStore();
  const logs = useLogsStore();

  function setScope(scope: string): void {
    const next = String(scope || "");
    if (next !== currentScope.value) {
      // 切换作用域时清除旧作用域的聚焦来源与选中状态，防止陷入空子图死胡同
      sourceFocus.value = "";
      selectedNode.value = "";
      selectedEdgeId.value = "";
      highlightedNode.value = "";
    }
    currentScope.value = next;
  }

  function setResolvedScope(scope: string): void {
    const next = String(scope || "");
    if (resolvedScope.value && next && resolvedScope.value !== next && !currentScope.value) {
      sourceFocus.value = "";
      selectedNode.value = "";
      selectedEdgeId.value = "";
      highlightedNode.value = "";
    }
    resolvedScope.value = next;
  }

  function setScopeOptions(options: ScopeOption[]): void {
    scopeOptions.value = Array.isArray(options) ? options : [];
  }

  function setNodeLabels(labels: string[]): void {
    nodeLabels.value = Array.isArray(labels) ? labels.filter(Boolean) : [];
  }

  /** 实际用于请求的 scope：选中非空则用选中，否则回退到解析的默认 scope。 */
  function effectiveScope(): string {
    return currentScope.value || resolvedScope.value || "";
  }

  // 当外部直接改 currentScope 时也确保清理旧作用域残留
  watch(currentScope, (next, prev) => {
    if (next !== prev) {
      sourceFocus.value = "";
      selectedNode.value = "";
      selectedEdgeId.value = "";
      highlightedNode.value = "";
    }
  });

  /** 加载 scope 列表（/api/scopes）。 */
  async function loadScopes(): Promise<void> {
    try {
      const data = await fetchScopes();
      const rawScopes = data.scopes || [];
      const scopes = rawScopes.filter((s) => s.value !== "default");
      setScopeOptions(scopes);
      setResolvedScope(data.current || "");
    } catch (err) {
      app.pushError(errText(err), "loadGraphScopes");
    }
  }

  function saveLayoutSnapshot(positions: Record<string, { x: number; y: number }>): void {
    layoutSnapshot.value = positions;
  }

  function clearSnapshot(): void {
    layoutSnapshot.value = {};
  }

  function resetGraphState(): void {
    rawNodes.value = [];
    rawEdges.value = [];
    nodeLabels.value = [];
    predicateLabels.value = [];
    highlightedNode.value = "";
    selectedNode.value = "";
    selectedEdgeId.value = "";
    userZoomed.value = false;
    zoom.value = 1;
    clearSnapshot();
  }

  let loadSeq = 0;

  async function loadGraph(): Promise<void> {
    const seq = ++loadSeq;
    loading.value = true;
    initError.value = "";
    try {
      const data = await fetchGraph({
        excludeLeaf: excludeLeaf.value,
        density: density.value,
        source: sourceFocus.value.trim() || undefined,
        scope: effectiveScope(),
      });
      if (seq !== loadSeq) return;
      rawNodes.value = data.nodes || [];
      rawEdges.value = data.edges || [];
      const labels = rawNodes.value
        .map((n) => n.label || n.id)
        .filter(Boolean)
        .sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
      setNodeLabels(labels);
      predicateLabels.value = Array.from(
        new Set(
          rawEdges.value.flatMap((e) => {
            const list: string[] = [];
            if (e.label) list.push(e.label);
            (e.predicates || []).forEach((p) => list.push(p));
            return list;
          }),
        ),
      ).sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
      highlightedNode.value = "";
      selectedNode.value = "";
      selectedEdgeId.value = "";
      userZoomed.value = false;
      logs.log(`图谱已载入：${rawNodes.value.length} 节点，${rawEdges.value.length} 边`, "info");
    } catch (err) {
      if (seq !== loadSeq) return;
      initError.value = errText(err);
      app.pushError(errText(err), "loadGraph");
      resetGraphState();
    } finally {
      if (seq === loadSeq) loading.value = false;
    }
  }

  // ===== 节点/边 CRUD =====

  async function addNode(nodeId: string, label?: string): Promise<boolean> {
    const cleanId = nodeId.trim();
    if (!cleanId) {
      app.pushError("请输入节点名称", "addNode");
      return false;
    }
    try {
      const cleanLabel = label?.trim() || undefined;
      await createNodeApi(cleanId, cleanLabel, effectiveScope());
      logs.log(`节点新增：${cleanLabel ? `${cleanLabel} (${cleanId})` : cleanId}`, "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "addNode");
      return false;
    }
  }

  async function removeNode(nodeId: string): Promise<boolean> {
    if (!nodeId) return false;
    try {
      await deleteNodeApi(nodeId, effectiveScope());
      logs.log(`节点删除：${nodeId}`, "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "deleteNode");
      return false;
    }
  }

  async function renameNode(oldId: string, newId: string): Promise<boolean> {
    if (!oldId || !newId.trim()) {
      app.pushError("请输入新节点名称", "renameNode");
      return false;
    }
    try {
      await renameNodeApi(oldId, newId.trim(), effectiveScope());
      logs.log(`节点重命名：${oldId} → ${newId.trim()}`, "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "renameNode");
      return false;
    }
  }

  async function addEdge(
    source: string,
    target: string,
    predicate?: string,
    weight = 1,
  ): Promise<boolean> {
    if (!source.trim() || !target.trim()) {
      app.pushError("请输入主体与客体", "addEdge");
      return false;
    }
    try {
      await createEdgeApi(
        source.trim(),
        target.trim(),
        weight,
        predicate?.trim(),
        effectiveScope(),
      );
      logs.log(`关系新增：${source.trim()} → ${target.trim()}`, "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "addEdge");
      return false;
    }
  }

  async function removeEdge(source: string, target: string): Promise<boolean> {
    if (!source || !target) return false;
    try {
      await deleteEdgeApi(source, target, effectiveScope());
      logs.log(`关系删除：${source} → ${target}`, "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "deleteEdge");
      return false;
    }
  }

  async function setEdgeWeight(source: string, target: string, weight: number): Promise<boolean> {
    if (!Number.isFinite(weight) || weight <= 0) {
      app.pushError("权重须为大于 0 的数值", "updateEdgeWeight");
      return false;
    }
    try {
      await updateEdgeWeightApi(source, target, weight, effectiveScope());
      logs.log(`关系权重更新：${source} → ${target} (${weight.toFixed(2)})`, "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "updateEdgeWeight");
      return false;
    }
  }

  return {
    currentScope,
    resolvedScope,
    scopeOptions,
    setScope,
    setResolvedScope,
    setScopeOptions,
    effectiveScope,
    loadScopes,
    rawNodes,
    rawEdges,
    nodeLabels,
    predicateLabels,
    sourceFocus,
    density,
    excludeLeaf,
    loading,
    initError,
    zoom,
    userZoomed,
    simulationRunning,
    lowPerf,
    highlightedNode,
    selectedNode,
    selectedEdgeId,
    layoutSnapshot,
    saveLayoutSnapshot,
    clearSnapshot,
    resetGraphState,
    loadGraph,
    addNode,
    removeNode,
    renameNode,
    addEdge,
    removeEdge,
    setEdgeWeight,
  };
});
