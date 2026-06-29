import { defineStore } from "pinia";
import { ref } from "vue";
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

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err ?? "未知错误");
}

/**
 * 图谱 store：scope（C5 全局唯一 source）+ 图谱业务状态 + CRUD actions。
 *
 * **currentScope 是全局唯一 scope source（修 C5）**：所有 scoped 请求（stats/dashboard/graph/node CRUD）
 * 统一读 effectiveScope()。legacy loadStats 用全局 scope、loadGraph 用下拉 scope → 新实现两者都读 currentScope。
 *
 * network 实例 + DataSet **不进 store**（vis 无响应式原生支持，包装触发无限更新），
 * 由 useVisNetwork composable 持有；store 只持业务状态（rawNodes/rawEdges/zoom/flag 等）。
 *
 * 修复点：
 * - C1：loadGraph try/catch，错误进总线 + 空状态降级（rawNodes 清空 + initError）。
 * - C4：loadGraph 成功后存 layoutSnapshot（节点坐标），restoreLayout 时回滚。
 * - 错误显式进 useAppStore 总线，不静默。
 */
export const useGraphStore = defineStore("graph", () => {
  // ===== scope（C5）=====
  /** 用户选中的 scope；"" 表示自动/最近（用解析的默认 scope）。 */
  const currentScope = ref("");
  /** /api/scopes.current：解析后的默认 scope（scope_resolver() = known_scopes[-1]）。 */
  const resolvedScope = ref("");
  /** 可选 scope 列表（来自 /api/scopes）。 */
  const scopeOptions = ref<ScopeOption[]>([]);

  // ===== 图谱业务状态 =====
  const rawNodes = ref<GraphNode[]>([]);
  const rawEdges = ref<GraphEdge[]>([]);
  /** 节点标签列表（候选菜单的 graph-node 候选源）。loadGraph 后填充。 */
  const nodeLabels = ref<string[]>([]);
  /** 谓词列表（候选菜单 predicate 候选源）。loadGraph 后从 rawEdges 标签收集。 */
  const predicateLabels = ref<string[]>([]);
  /** 用户选中的来源过滤（""=全部图谱）。 */
  const sourceFocus = ref("");
  /** 信息密度（legacy graph-density，0.1-1，默认 0.82）。 */
  const density = ref(0.82);
  /** 是否过滤叶子节点。 */
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

  /** C4：布局快照（loadGraph 后存节点坐标，restoreLayout 时回滚）。 */
  const layoutSnapshot = ref<Record<string, { x: number; y: number }>>({});

  const app = useAppStore();
  const logs = useLogsStore();

  function setScope(scope: string): void {
    currentScope.value = String(scope || "");
  }

  function setResolvedScope(scope: string): void {
    resolvedScope.value = String(scope || "");
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

  /** 加载 scope 列表（/api/scopes，bridge 层，与 _scope 无关）。 */
  async function loadScopes(): Promise<void> {
    try {
      const data = await fetchScopes();
      // 后端在无会话触发 runtime 时兜底返回字面量 "default"（scope_resolver 兜底），
      // 这是内部作用域标识，不该原样展示给用户。下拉已有静态「自动 / 最近」option（value=""）
      // 对应 currentScope 为空→effectiveScope 回退 resolvedScope(default) 的语义，
      // 故过滤掉 default 项避免重复展示；保留真实作用域（如 aiocqhttp:group:xxx）。
      const rawScopes = data.scopes || [];
      const scopes = rawScopes.filter((s) => s.value !== "default");
      setScopeOptions(scopes);
      setResolvedScope(data.current || "");
    } catch (err) {
      app.pushError(errText(err), "loadGraphScopes");
    }
  }

  /** 保存快照（C4：useVisNetwork 在 loadGraph fit 后调用，存当前节点坐标）。 */
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

  /**
   * 加载图谱（C5：统一用 effectiveScope；C1：try/catch + 空状态降级）。
   * 注意：成功后 useVisNetwork 负责把 rawNodes/rawEdges 灌入 DataSet 与 fit，
   * store 仅持久化数据 + 填充候选标签 + 存快照前的清空。
   */
  async function loadGraph(): Promise<void> {
    loading.value = true;
    initError.value = "";
    try {
      const data = await fetchGraph({
        excludeLeaf: excludeLeaf.value,
        density: density.value,
        source: sourceFocus.value.trim() || undefined,
        scope: effectiveScope(),
      });
      rawNodes.value = data.nodes || [];
      rawEdges.value = data.edges || [];
      // 填充候选标签（P5/P6/Query 依赖 nodeLabels，P7 relation 依赖 predicateLabels）
      const labels = rawNodes.value
        .map((n) => n.label || n.id)
        .filter(Boolean)
        .sort((a, b) => String(a).localeCompare(String(b), "zh-CN"))
        .slice(0, 500);
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
      )
        .sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
      highlightedNode.value = "";
      selectedNode.value = "";
      selectedEdgeId.value = "";
      userZoomed.value = false;
      logs.log(`图谱已载入：${rawNodes.value.length} 节点，${rawEdges.value.length} 边`, "info");
    } catch (err) {
      // C1：错误进总线 + 空状态降级
      initError.value = errText(err);
      app.pushError(errText(err), "loadGraph");
      resetGraphState();
    } finally {
      loading.value = false;
    }
  }

  // ===== 节点/边 CRUD（成功后由调用方触发 loadGraph 刷新视图）=====

  async function addNode(nodeId: string): Promise<boolean> {
    if (!nodeId.trim()) {
      app.pushError("请输入节点名称", "addNode");
      return false;
    }
    try {
      await createNodeApi(nodeId.trim(), undefined, effectiveScope());
      logs.log(`节点新增：${nodeId.trim()}`, "info");
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
      await createEdgeApi(source.trim(), target.trim(), weight, predicate?.trim(), effectiveScope());
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
    try {
      await updateEdgeWeightApi(source, target, weight, effectiveScope());
      return true;
    } catch (err) {
      app.pushError(errText(err), "updateEdgeWeight");
      return false;
    }
  }

  return {
    // scope（C5）
    currentScope,
    resolvedScope,
    scopeOptions,
    setScope,
    setResolvedScope,
    setScopeOptions,
    effectiveScope,
    loadScopes,
    // 业务状态
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
    // actions
    loadGraph,
    addNode,
    removeNode,
    renameNode,
    addEdge,
    removeEdge,
    setEdgeWeight,
  };
});
