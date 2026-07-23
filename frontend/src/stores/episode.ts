import { defineStore } from "pinia";
import { ref } from "vue";
import {
  deleteParagraphByHash as deleteParagraphApi,
  fetchEpisode,
  queryEpisodes,
  rebuildEpisode as rebuildEpisodeApi,
  type Episode,
} from "@/services/episodeApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";


/**
 * Episode store：情景记忆列表 + 详情 + 按 source 重建。
 *
 * - 修复 H4：query/detail 各持单调请求序号，过期请求丢弃，避免快速切换错位。
 * - scope：/v1/episode/* 经 bridge _scope 路由，统一传 graph.effectiveScope()。
 * - 错误显式进 useAppStore 总线。
 * - episode 重建返回 409（已在运行）时如实提示，不静默。
 */
export const useEpisodeStore = defineStore("episode", () => {
  const list = ref<Episode[]>([]);
  const count = ref(0);
  const detail = ref<Episode | null>(null);
  const meta = ref("");
  const query = ref("");
  const source = ref("");
  const topk = ref(10);
  const rebuildSource = ref("");
  const rebuildResult = ref<Record<string, unknown> | null>(null);
  const loading = ref(false);
  const loadingDetail = ref(false);
  const rebuilding = ref(false);
  const deleting = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  let listSeq = 0;
  let detailSeq = 0;

  async function loadList(): Promise<void> {
    listSeq += 1;
    const seq = listSeq;
    loading.value = true;
    try {
      const data = await queryEpisodes(
        {
          query: query.value.trim(),
          source: source.value.trim() || null,
          top_k: Number(topk.value) || 10,
          include_paragraphs: false,
        },
        graph.effectiveScope(),
      );
      if (seq !== listSeq) return; // 旧请求丢弃（修 H4）
      list.value = data.results || [];
      count.value = data.count ?? list.value.length;
      meta.value = `${count.value} items`;
    } catch (err) {
      if (seq === listSeq) {
        app.pushError(errText(err), "loadEpisodes");
        list.value = [];
        meta.value = "0 items";
      }
    } finally {
      if (seq === listSeq) loading.value = false;
    }
  }

  async function loadDetail(episodeId: string): Promise<void> {
    if (!episodeId) return;
    detailSeq += 1;
    const seq = detailSeq;
    loadingDetail.value = true;
    try {
      const data = await fetchEpisode(episodeId, true, graph.effectiveScope());
      if (seq !== detailSeq) return; // 旧请求丢弃（修 H4 错位）
      detail.value = data;
    } catch (err) {
      if (seq === detailSeq) app.pushError(errText(err), "loadEpisodeDetail");
    } finally {
      if (seq === detailSeq) loadingDetail.value = false;
    }
  }

  async function rebuild(): Promise<boolean> {
    const src = rebuildSource.value.trim();
    if (!src) {
      app.pushError("请填写 source", "rebuildEpisode");
      return false;
    }
    rebuilding.value = true;
    try {
      const data = await rebuildEpisodeApi(src, graph.effectiveScope());
      rebuildResult.value = data as Record<string, unknown>;
      logs.log(`Episode 重建：${src}`, "info");
      await loadList(); // 刷新列表
      return true;
    } catch (err) {
      app.pushError(errText(err), "rebuildEpisode");
      return false;
    } finally {
      rebuilding.value = false;
    }
  }

  /** 删除 episode 关联的某段落（episode 详情内）。 */
  async function removeParagraph(hash: string): Promise<boolean> {
    if (!hash) return false;
    const confirmed = await app.requestConfirmation({
      title: "删除段落",
      message: "确定删除该段落？相关图谱关系可能被同步剪枝。",
      confirmText: "删除",
      danger: true,
    });
    if (!confirmed) return false;
    deleting.value = true;
    try {
      const data = await deleteParagraphApi(hash, graph.effectiveScope());
      logs.log(`段落删除：剪枝 ${data?.relation_prune_count ?? 0} 关系`, "info");
      // 刷新当前详情（若已加载）
      if (detail.value?.episode_id) await loadDetail(detail.value.episode_id);
      return true;
    } catch (err) {
      app.pushError(errText(err), "deleteParagraph");
      return false;
    } finally {
      deleting.value = false;
    }
  }

  return {
    list,
    count,
    detail,
    meta,
    query,
    source,
    topk,
    rebuildSource,
    rebuildResult,
    loading,
    loadingDetail,
    rebuilding,
    deleting,
    loadList,
    loadDetail,
    rebuild,
    removeParagraph,
  };
});
