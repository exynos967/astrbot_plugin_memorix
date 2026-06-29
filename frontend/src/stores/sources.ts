import { defineStore } from "pinia";
import { ref } from "vue";
import {
  batchDeleteSource as batchDeleteApi,
  deleteParagraph as deleteParagraphApi,
  fetchSourceList,
  type SourceListItem,
  type SourceListResult,
} from "@/services/sourceApi";
import { isSummaryMode, sourceNameOf } from "@/utils/sourceText";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err ?? "未知错误");
}

/**
 * Sources store：来源/段落列表 + 删除。
 *
 * 后端 /api/source/* 与 /v1/delete/paragraph 均 NOT scope-aware，直接请求。
 * 修复 H4：load 用单调请求序号，await 后若已过期则丢弃旧结果，避免快速切换错位。
 * 错误显式进总线。
 */
export const useSourcesStore = defineStore("sources", () => {
  const items = ref<SourceListItem[]>([]);
  const mode = ref<string>("");
  const meta = ref("");
  const nodeId = ref("");
  const edgeFrom = ref("");
  const edgeTo = ref("");
  const loading = ref(false);
  const deleting = ref(false);
  const lastResult = ref<SourceListResult | null>(null);

  const app = useAppStore();
  const logs = useLogsStore();

  let reqSeq = 0;

  async function load(): Promise<void> {
    reqSeq += 1;
    const seq = reqSeq;
    loading.value = true;
    try {
      const data = await fetchSourceList({
        node_id: nodeId.value.trim() || null,
        edge_source: edgeFrom.value.trim() || null,
        edge_target: edgeTo.value.trim() || null,
      });
      if (seq !== reqSeq) return; // 旧请求丢弃（修 H4 错位）
      lastResult.value = data;
      mode.value = data.mode || "";
      const list = Array.isArray(data.sources) ? data.sources : [];
      if (isSummaryMode(data)) {
        const filtered = list.filter((it) => sourceNameOf(it));
        items.value = filtered;
        meta.value = `${filtered.length} sources`;
      } else {
        items.value = list;
        meta.value = `${list.length} paragraphs`;
      }
    } catch (err) {
      if (seq === reqSeq) app.pushError(errText(err), "loadSources");
    } finally {
      if (seq === reqSeq) loading.value = false;
    }
  }

  async function removeSource(source: string): Promise<boolean> {
    if (!source || !window.confirm(`删除 source ${source}？`)) return false;
    deleting.value = true;
    try {
      const data = await batchDeleteApi(source);
      logs.log(`source 删除：${data?.count ?? 0} 段`, "info");
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
    if (!hash || !window.confirm("删除该段落？")) return false;
    deleting.value = true;
    try {
      const data = await deleteParagraphApi(hash);
      logs.log(`段落删除：剪枝 ${data?.relation_prune_count ?? 0} 关系`, "info");
      await load();
      return true;
    } catch (err) {
      app.pushError(errText(err), "deleteParagraph");
      return false;
    } finally {
      deleting.value = false;
    }
  }

  return {
    items,
    mode,
    meta,
    nodeId,
    edgeFrom,
    edgeTo,
    loading,
    deleting,
    load,
    removeSource,
    removeParagraph,
  };
});
