import { defineStore } from "pinia";
import { ref, watch } from "vue";
import {
  deleteParagraphByHash as deleteParagraphApi,
  episodeContractAdmin,
  fetchEpisode,
  queryEpisodes,
  type Episode,
  type EpisodeLeaseStatus,
  type EpisodeRebuildResult,
} from "@/services/episodeApi";
import { fetchSourceList } from "@/services/sourceApi";
import { sourceNameOf } from "@/utils/sourceText";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";

/**
 * Episode store：情景记忆检索列表 + 详情 + Lease 安全重建队列管理。
 * - 支持 query / source / person / time_from / time_to 多维筛选。
 * - 重建全面走 POST /v1/admin/memory_episode_admin 租约安全契约。
 * - 监听 graph.effectiveScope()，切换作用域时清空旧数据并自动重载。
 */
export const useEpisodeStore = defineStore("episode", () => {
  const list = ref<Episode[]>([]);
  const count = ref(0);
  const detail = ref<Episode | null>(null);
  const meta = ref("等待加载");

  const query = ref("");
  const source = ref("");
  const person = ref("");
  const timeFrom = ref("");
  const timeTo = ref("");
  const topk = ref(10);

  const rebuildSource = ref("");
  const rebuildResult = ref<EpisodeRebuildResult | null>(null);
  const leaseStatus = ref<EpisodeLeaseStatus | null>(null);

  const loading = ref(false);
  const loadingDetail = ref(false);
  const loadingStatus = ref(false);
  const rebuilding = ref(false);
  const queueBusy = ref(false);
  const deleting = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  let initialized = false;
  let listSeq = 0;
  let detailSeq = 0;
  let statusSeq = 0;

  // 按作用域缓存真实来源候选列表，供检索与重建输入框自动补全复用
  let cachedSourceScope: string | null = null;
  let cachedSources: string[] = [];
  let pendingSourcePromise: Promise<string[]> | null = null;

  function invalidateSourceCache(): void {
    cachedSourceScope = null;
    cachedSources = [];
    pendingSourcePromise = null;
  }

  async function loadRealSources(scope: string): Promise<string[]> {
    if (cachedSourceScope === scope && cachedSources.length > 0) {
      return cachedSources;
    }
    if (pendingSourcePromise && cachedSourceScope === scope) {
      return pendingSourcePromise;
    }
    cachedSourceScope = scope;
    pendingSourcePromise = fetchSourceList({}, scope)
      .then((res) => {
        const names = Array.from(
          new Set((res.sources || []).map((it) => sourceNameOf(it)).filter(Boolean)),
        ).sort((a, b) => a.localeCompare(b, "zh-CN"));
        if (cachedSourceScope === scope) {
          cachedSources = names;
        }
        return names;
      })
      .catch(() => [])
      .finally(() => {
        pendingSourcePromise = null;
      });
    return pendingSourcePromise;
  }

  async function getSourceCandidates(kw: string): Promise<string[]> {
    const all = await loadRealSources(graph.effectiveScope());
    const needle = kw.trim().toLowerCase();
    if (!needle) return all.slice(0, 40);
    return all.filter((s) => s.toLowerCase().includes(needle)).slice(0, 40);
  }

  async function loadList(): Promise<void> {
    initialized = true;
    listSeq += 1;
    const seq = listSeq;
    loading.value = true;
    const scope = graph.effectiveScope();
    try {
      const data = await queryEpisodes(
        {
          query: query.value.trim(),
          source: source.value.trim() || null,
          person: person.value.trim() || null,
          time_from: timeFrom.value.trim() || null,
          time_to: timeTo.value.trim() || null,
          top_k: Number(topk.value) || 10,
          include_paragraphs: false,
        },
        scope,
      );
      if (seq !== listSeq) return;
      list.value = data.results || [];
      count.value = data.count ?? list.value.length;
      meta.value = `共 ${count.value} 条情景`;
      // 若当前选中的详情已不在当前作用域，保持独立；若首次加载无详情且有结果不强行覆盖用户选择
    } catch (err) {
      if (seq === listSeq) {
        app.pushError(errText(err), "loadEpisodes");
        list.value = [];
        count.value = 0;
        meta.value = "共 0 条情景";
      }
    } finally {
      if (seq === listSeq) loading.value = false;
    }
  }

  async function resetFilters(): Promise<void> {
    query.value = "";
    source.value = "";
    person.value = "";
    timeFrom.value = "";
    timeTo.value = "";
    topk.value = 10;
    await loadList();
  }

  async function loadDetail(episodeId: string): Promise<void> {
    if (!episodeId) return;
    detailSeq += 1;
    const seq = detailSeq;
    loadingDetail.value = true;
    try {
      const data = await fetchEpisode(episodeId, true, graph.effectiveScope());
      if (seq !== detailSeq) return;
      detail.value = data;
    } catch (err) {
      if (seq === detailSeq) app.pushError(errText(err), "loadEpisodeDetail");
    } finally {
      if (seq === detailSeq) loadingDetail.value = false;
    }
  }

  /** 拉取 v2.0 情景生成租约队列状态（pending / running / failed）。 */
  async function loadLeaseStatus(): Promise<void> {
    initialized = true;
    statusSeq += 1;
    const seq = statusSeq;
    loadingStatus.value = true;
    try {
      const res = await episodeContractAdmin<EpisodeLeaseStatus>(
        "status",
        { limit: 10 },
        graph.effectiveScope(),
      );
      if (seq === statusSeq) {
        leaseStatus.value = res;
      }
    } catch (err) {
      if (seq === statusSeq) {
        app.pushError(errText(err), "loadEpisodeLeaseStatus");
      }
    } finally {
      if (seq === statusSeq) loadingStatus.value = false;
    }
  }

  /** 在线租约安全重建：指定 source 或全量重建（forceAll=true）。 */
  async function rebuild(forceAll = false): Promise<boolean> {
    const src = rebuildSource.value.trim();
    if (!forceAll && !src) {
      app.pushError("请填写要重建的来源名称，或使用「全量重建」", "rebuildEpisode");
      return false;
    }
    if (forceAll) {
      const ok = await app.requestConfirmation({
        title: "全量重建情景记忆",
        message:
          "确定对当前作用域下的全部来源执行租约安全重建？未变更的事件分组将自动复用缓存。",
        confirmText: "开始全量重建",
        danger: false,
      });
      if (!ok) return false;
    }

    rebuilding.value = true;
    const scope = graph.effectiveScope();
    try {
      const payload = forceAll ? { all: true } : { source: src };
      const data = await episodeContractAdmin<EpisodeRebuildResult>("rebuild", payload, scope);
      if (scope === graph.effectiveScope()) {
        rebuildResult.value = data;
      }
      const label = forceAll ? "全部来源" : src;
      logs.log(`情景记忆重建完成：${label}`, "info");
      app.pushSuccess?.(`情景重建完成（${label}）`, "rebuildEpisode");
      invalidateSourceCache();
      await Promise.allSettled([
        loadList(),
        loadLeaseStatus(),
        detail.value?.episode_id ? loadDetail(detail.value.episode_id) : Promise.resolve(),
      ]);
      return true;
    } catch (err) {
      app.pushError(errText(err), "rebuildEpisode");
      return false;
    } finally {
      rebuilding.value = false;
    }
  }

  /** 推进待处理情景队列（优先走 process_pending，兼容回退 process_sources）。 */
  async function processPending(limit = 10): Promise<boolean> {
    if (queueBusy.value) return false;
    queueBusy.value = true;
    const scope = graph.effectiveScope();
    try {
      let data: EpisodeRebuildResult;
      try {
        data = await episodeContractAdmin<EpisodeRebuildResult>(
          "process_pending",
          { limit },
          scope,
        );
      } catch (firstErr) {
        const msg = errText(firstErr);
        if (msg.includes("process_pending") || msg.includes("不支持") || msg.includes("action")) {
          data = await episodeContractAdmin<EpisodeRebuildResult>(
            "process_sources",
            { limit },
            scope,
          );
        } else {
          throw firstErr;
        }
      }
      if (scope === graph.effectiveScope()) {
        rebuildResult.value = data;
      }
      app.pushSuccess?.("待处理情景队列已执行一轮推进", "episodeQueue");
      invalidateSourceCache();
      await Promise.allSettled([loadList(), loadLeaseStatus()]);
      return true;
    } catch (err) {
      app.pushError(errText(err), "processPendingEpisodes");
      return false;
    } finally {
      queueBusy.value = false;
    }
  }

  /** 丢弃历史迁移回填积压（discard_migration_backfill）。 */
  async function discardMigrationBackfill(): Promise<boolean> {
    if (queueBusy.value) return false;
    const confirmed = await app.requestConfirmation({
      title: "清理迁移回填积压",
      message:
        "确定丢弃旧版升级遗留的待回填情景任务队列？已生成的正常情景记忆不会受影响。",
      confirmText: "确认清理",
      danger: true,
    });
    if (!confirmed) return false;

    queueBusy.value = true;
    const scope = graph.effectiveScope();
    try {
      const data = await episodeContractAdmin<EpisodeRebuildResult>(
        "discard_migration_backfill",
        { dry_run: false },
        scope,
      );
      if (scope === graph.effectiveScope()) {
        rebuildResult.value = data;
      }
      app.pushSuccess?.("已清理迁移回填积压队列", "episodeQueue");
      await loadLeaseStatus();
      return true;
    } catch (err) {
      app.pushError(errText(err), "discardMigrationBackfill");
      return false;
    } finally {
      queueBusy.value = false;
    }
  }

  /** 删除情景关联段落，并同步刷新列表与详情。 */
  async function removeParagraph(hash: string): Promise<boolean> {
    if (!hash) return false;
    const confirmed = await app.requestConfirmation({
      title: "删除关联段落",
      message: "确定删除该原始记忆段落？关联的图谱关系与情景引用将被同步剪枝。",
      confirmText: "确认删除",
      danger: true,
    });
    if (!confirmed) return false;

    deleting.value = true;
    const scope = graph.effectiveScope();
    try {
      const data = await deleteParagraphApi(hash, scope);
      const pruned = data?.deleted?.relation ?? data?.relation_prune_count ?? 0;
      logs.log(`段落已删除：同步剪枝 ${pruned} 条图谱关系`, "info");
      app.pushSuccess?.(`段落已删除（同步剪枝 ${pruned} 条关系）`, "deleteParagraph");
      invalidateSourceCache();

      const currentEpId = detail.value?.episode_id || "";
      await loadList();
      if (currentEpId) {
        try {
          const nextDetail = await fetchEpisode(currentEpId, true, scope);
          detail.value = nextDetail;
        } catch {
          // 若该情景下所有段落均已被删空，情景可能已被回收，清空右侧详情即可
          detail.value = null;
        }
      }
      return true;
    } catch (err) {
      app.pushError(errText(err), "deleteParagraph");
      return false;
    } finally {
      deleting.value = false;
    }
  }

  // 监听作用域切换：清空旧作用域列表/详情/重建快照并自动刷新
  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (next === prev) return;
      listSeq += 1;
      detailSeq += 1;
      statusSeq += 1;
      invalidateSourceCache();
      list.value = [];
      count.value = 0;
      detail.value = null;
      meta.value = "等待加载";
      rebuildResult.value = null;
      leaseStatus.value = null;
      if (initialized) {
        void Promise.allSettled([loadList(), loadLeaseStatus()]);
      }
    },
  );

  return {
    list,
    count,
    detail,
    meta,
    query,
    source,
    person,
    timeFrom,
    timeTo,
    topk,
    rebuildSource,
    rebuildResult,
    leaseStatus,
    loading,
    loadingDetail,
    loadingStatus,
    rebuilding,
    queueBusy,
    deleting,
    getSourceCandidates,
    loadList,
    resetFilters,
    loadDetail,
    loadLeaseStatus,
    rebuild,
    processPending,
    discardMigrationBackfill,
    removeParagraph,
  };
});
