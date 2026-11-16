import { defineStore } from "pinia";
import { ref, watch } from "vue";
import {
  batchDeleteSource as batchDeleteApi,
  deleteParagraph as deleteParagraphApi,
  fetchSourceList,
  type SourceListItem,
  type SourceListResult,
  type SourceParagraphItem,
} from "@/services/sourceApi";
import { isParagraph, isSummaryMode, sourceNameOf } from "@/utils/sourceText";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";

/**
 * Sources store：来源汇总 + 按节点/边筛选段落 + 点击来源行内联展开段落明细 + 删除管理。
 * - 修复严重跨作用域泄漏与误删：fetchSourceList / batchDeleteSource 全量传入 graph.effectiveScope()。
 * - 校验边筛选成对输入，防止单填起点或终点时静默返回空结果。
 * - 监听 graph.effectiveScope()，切换作用域时清空旧数据并自动刷新。
 */
export const useSourcesStore = defineStore("sources", () => {
  const items = ref<SourceListItem[]>([]);
  const mode = ref<string>("");
  const meta = ref("等待加载");
  const nodeId = ref("");
  const edgeFrom = ref("");
  const edgeTo = ref("");
  const loading = ref(false);
  const deleting = ref(false);
  const lastResult = ref<SourceListResult | null>(null);

  // Summary 模式下点击某个来源行内联展开的段落明细
  const expandedSource = ref("");
  const expandedParagraphs = ref<SourceParagraphItem[]>([]);
  const loadingExpanded = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  let initialized = false;
  let reqSeq = 0;
  let expandSeq = 0;

  async function load(): Promise<void> {
    const ef = edgeFrom.value.trim();
    const et = edgeTo.value.trim();
    if (Boolean(ef) !== Boolean(et)) {
      app.pushError("按边筛选时需同时填写「边起点」与「边终点」", "loadSources");
      return;
    }

    initialized = true;
    reqSeq += 1;
    const seq = reqSeq;
    loading.value = true;
    const scope = graph.effectiveScope();

    try {
      const data = await fetchSourceList(
        {
          node_id: nodeId.value.trim() || null,
          edge_source: ef || null,
          edge_target: et || null,
        },
        scope,
      );
      if (seq !== reqSeq) return;
      lastResult.value = data;
      mode.value = data.mode || "";
      const list = Array.isArray(data.sources) ? data.sources : [];
      if (isSummaryMode(data)) {
        const filtered = list.filter((it) => sourceNameOf(it));
        items.value = filtered;
        meta.value = `共 ${filtered.length} 个来源`;
        // 若当前展开的来源已被删空，自动收起
        if (
          expandedSource.value &&
          !filtered.some((it) => sourceNameOf(it) === expandedSource.value)
        ) {
          expandedSource.value = "";
          expandedParagraphs.value = [];
        }
      } else {
        expandedSource.value = "";
        expandedParagraphs.value = [];
        items.value = list;
        meta.value = `共 ${list.length} 条段落`;
      }
    } catch (err) {
      if (seq === reqSeq) app.pushError(errText(err), "loadSources");
    } finally {
      if (seq === reqSeq) loading.value = false;
    }
  }

  async function resetFilters(): Promise<void> {
    nodeId.value = "";
    edgeFrom.value = "";
    edgeTo.value = "";
    expandedSource.value = "";
    expandedParagraphs.value = [];
    await load();
  }

  /** 在汇总模式下展开/收起指定来源的段落明细。 */
  async function toggleExpandSource(source: string): Promise<void> {
    const clean = source.trim();
    if (!clean) return;
    if (expandedSource.value === clean) {
      expandedSource.value = "";
      expandedParagraphs.value = [];
      loadingExpanded.value = false;
      return;
    }
    await loadExpandedParagraphs(clean);
  }

  async function loadExpandedParagraphs(source: string): Promise<void> {
    expandedSource.value = source;
    expandedParagraphs.value = [];
    expandSeq += 1;
    const seq = expandSeq;
    loadingExpanded.value = true;
    try {
      const data = await fetchSourceList({ source }, graph.effectiveScope());
      if (seq !== expandSeq || expandedSource.value !== source) return;
      const list = Array.isArray(data.sources) ? data.sources : [];
      expandedParagraphs.value = list.filter(isParagraph);
    } catch (err) {
      if (seq === expandSeq) {
        app.pushError(errText(err), "loadSourceParagraphs");
      }
    } finally {
      if (seq === expandSeq) loadingExpanded.value = false;
    }
  }

  async function removeSource(source: string): Promise<boolean> {
    if (!source) return false;
    const scope = graph.effectiveScope();
    const confirmed = await app.requestConfirmation({
      title: "删除记忆来源",
      message: `确定删除当前作用域下的来源「${source}」及其关联段落与孤立图谱关系？`,
      confirmText: "确认删除",
      danger: true,
    });
    if (!confirmed) return false;

    deleting.value = true;
    try {
      const data = await batchDeleteApi(source, scope);
      const pCount = data?.deleted?.paragraph ?? data?.count ?? 0;
      const rCount = data?.deleted?.relation ?? 0;
      logs.log(
        `来源已删除 [${source}]：清理 ${pCount} 条段落，剪枝 ${rCount} 条关系`,
        "info",
      );
      app.pushSuccess?.(`已删除来源「${source}」（清理 ${pCount} 条段落）`, "deleteSource");
      if (expandedSource.value === source) {
        expandedSource.value = "";
        expandedParagraphs.value = [];
      }
      await load();
      return true;
    } catch (err) {
      app.pushError(errText(err), "deleteSource");
      return false;
    } finally {
      deleting.value = false;
    }
  }

  async function removeParagraph(hash: string): Promise<boolean> {
    if (!hash) return false;
    const scope = graph.effectiveScope();
    const confirmed = await app.requestConfirmation({
      title: "删除记忆段落",
      message: "确定删除该记忆段落？相关图谱关系将被同步剪枝。",
      confirmText: "确认删除",
      danger: true,
    });
    if (!confirmed) return false;

    deleting.value = true;
    try {
      const data = await deleteParagraphApi(hash, scope);
      const pruned = data?.deleted?.relation ?? data?.relation_prune_count ?? 0;
      logs.log(`段落已删除：同步剪枝 ${pruned} 条关系`, "info");
      app.pushSuccess?.(`段落已删除（同步剪枝 ${pruned} 条关系）`, "deleteParagraph");
      const activeExpanded = expandedSource.value;
      await load();
      if (activeExpanded && expandedSource.value === activeExpanded) {
        await loadExpandedParagraphs(activeExpanded);
      }
      return true;
    } catch (err) {
      app.pushError(errText(err), "deleteParagraph");
      return false;
    } finally {
      deleting.value = false;
    }
  }

  // 监听作用域切换：清空旧作用域数据并刷新
  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (next === prev) return;
      reqSeq += 1;
      expandSeq += 1;
      items.value = [];
      lastResult.value = null;
      mode.value = "";
      meta.value = "等待加载";
      expandedSource.value = "";
      expandedParagraphs.value = [];
      loadingExpanded.value = false;
      if (initialized) {
        void load();
      }
    },
  );

  return {
    items,
    mode,
    meta,
    nodeId,
    edgeFrom,
    edgeTo,
    loading,
    deleting,
    lastResult,
    expandedSource,
    expandedParagraphs,
    loadingExpanded,
    load,
    resetFilters,
    toggleExpandSource,
    removeSource,
    removeParagraph,
  };
});
