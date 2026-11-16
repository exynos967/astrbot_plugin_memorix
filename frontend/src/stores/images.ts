import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";
import {
  classifyImageMemoryDisabled,
  extractDataUrlFromRead,
  imageApi,
  resolveObservationId,
  type ImageAssetItem,
  type ImageChatStat,
  type ImageDetailResponse,
  type ImageObservation,
  type ImageQueueJob,
  type ImageSearchHit,
  type ImageStatusPayload,
} from "@/services/imageApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { errText } from "@/utils/error";

export const IMAGE_PAGE_SIZE = 24;

export const useImagesStore = defineStore("images", () => {
  const graph = useGraphStore();
  const app = useAppStore();

  const statusInfo = ref<ImageStatusPayload | null>(null);
  const assets = ref<ImageAssetItem[]>([]);
  const chatStats = ref<ImageChatStat[]>([]);
  const selectedChatId = ref("");
  const page = ref(1);
  const loading = ref(false);

  // asset_id -> data_url 懒加载缓存与飞行中去重
  const previewMap = ref<Map<string, string>>(new Map());
  const previewPending = new Set<string>();
  const previewFailed = ref<Set<string>>(new Set());

  // 详情弹窗状态
  const activeAssetId = ref("");
  const detailLoading = ref(false);
  const activeDetail = ref<ImageDetailResponse | null>(null);

  // 以图搜图状态
  const searchOpen = ref(false);
  const searching = ref(false);
  const similarityThreshold = ref(0.72);
  const searchHits = ref<ImageSearchHit[]>([]);
  const searchTimings = ref<Record<string, number> | null>(null);
  const searchSourceLabel = ref("");
  const searchHash = ref("");
  const searchBase64 = ref("");
  const searchStatus = ref("");

  // 后台队列维护弹窗状态
  const jobsModalOpen = ref(false);
  const jobsLoading = ref(false);
  const jobsActionBusy = ref(false);
  const jobStatusFilter = ref("");
  const jobs = ref<ImageQueueJob[]>([]);
  const jobsTotal = ref(0);

  let loadSeq = 0;
  let detailSeq = 0;
  let searchSeq = 0;
  let jobsSeq = 0;

  const scope = computed(() => graph.effectiveScope());

  const runtimeStatus = computed(() => statusInfo.value?.status || "ready");
  const disabledInfo = computed(() => classifyImageMemoryDisabled(statusInfo.value));
  const isDisabled = computed(() => disabledInfo.value.disabled);
  const embeddingConfigured = computed(() => statusInfo.value?.embedding_configured !== false);

  const totalAssetCount = computed(() => {
    if (selectedChatId.value) {
      const hit = chatStats.value.find((c) => c.chat_id === selectedChatId.value);
      if (hit) return hit.asset_count;
    }
    return statusInfo.value?.stats?.asset_count ?? assets.value.length;
  });

  const totalPages = computed(() =>
    Math.max(1, Math.ceil(Math.max(totalAssetCount.value, assets.value.length) / IMAGE_PAGE_SIZE)),
  );

  async function refreshAll(resetPage = false): Promise<void> {
    if (resetPage) page.value = 1;
    const seq = ++loadSeq;
    const targetScope = scope.value;
    loading.value = true;
    try {
      // 先读取 status，确保在未配置图片嵌入或手动关闭时平静呈现引导态，绝不弹出错误 Toast
      const st = await imageApi.getStatus(targetScope);
      if (seq !== loadSeq) return;

      if (st.enabled === false || st.status === "disabled") {
        statusInfo.value = st;
        assets.value = [];
        chatStats.value = [];
        return;
      }

      const offset = (page.value - 1) * IMAGE_PAGE_SIZE;
      const [listRes, chatsRes] = await Promise.all([
        imageApi.listAssets(
          {
            limit: IMAGE_PAGE_SIZE,
            offset,
            chat_id: selectedChatId.value || undefined,
          },
          targetScope,
        ),
        imageApi.listChats(targetScope).catch(() => ({ items: [] as ImageChatStat[] })),
      ]);
      if (seq !== loadSeq) return;

      statusInfo.value = {
        ...st,
        ...listRes,
        enabled: listRes.enabled ?? st.enabled,
        configured_enabled: listRes.configured_enabled ?? st.configured_enabled,
        embedding_configured: listRes.embedding_configured ?? st.embedding_configured,
        require_embedding: listRes.require_embedding ?? st.require_embedding,
        paused_by_missing_embedding:
          listRes.paused_by_missing_embedding ?? st.paused_by_missing_embedding,
        reason: listRes.reason ?? st.reason,
        stats: listRes.stats ?? st.stats,
        index_progress: listRes.index_progress ?? st.index_progress,
      };
      assets.value = Array.isArray(listRes.items) ? listRes.items : [];
      chatStats.value = Array.isArray(chatsRes.items) ? chatsRes.items : [];
    } catch (err) {
      if (seq === loadSeq) {
        app.pushError(errText(err), "图片记忆");
      }
    } finally {
      if (seq === loadSeq) {
        loading.value = false;
      }
    }
  }

  async function ensurePreview(assetId: string): Promise<void> {
    if (!assetId || previewMap.value.has(assetId) || previewPending.has(assetId)) return;
    const targetScope = scope.value;
    previewPending.add(assetId);
    try {
      const res = await imageApi.readAssetPreview(assetId, targetScope);
      if (targetScope !== scope.value) return;
      const dataUrl = extractDataUrlFromRead(res);
      if (dataUrl) {
        const next = new Map(previewMap.value);
        next.set(assetId, dataUrl);
        previewMap.value = next;
      } else {
        const nextFail = new Set(previewFailed.value);
        nextFail.add(assetId);
        previewFailed.value = nextFail;
      }
    } catch {
      if (targetScope === scope.value) {
        const nextFail = new Set(previewFailed.value);
        nextFail.add(assetId);
        previewFailed.value = nextFail;
      }
    } finally {
      previewPending.delete(assetId);
    }
  }

  function getPreviewUrl(assetId: string): string {
    return previewMap.value.get(assetId) || "";
  }

  async function setChatFilter(chatId: string): Promise<void> {
    selectedChatId.value = chatId;
    await refreshAll(true);
  }

  async function goToPage(nextPage: number): Promise<void> {
    const clamped = Math.max(1, Math.min(totalPages.value, nextPage));
    if (clamped === page.value) return;
    page.value = clamped;
    await refreshAll(false);
  }

  async function openDetail(assetId: string): Promise<void> {
    if (!assetId) return;
    const seq = ++detailSeq;
    activeAssetId.value = assetId;
    activeDetail.value = null;
    detailLoading.value = true;
    void ensurePreview(assetId);
    try {
      const res = await imageApi.getAssetDetail(
        assetId,
        selectedChatId.value || undefined,
        scope.value,
      );
      if (seq !== detailSeq) return;
      activeDetail.value = res;
    } catch (err) {
      if (seq !== detailSeq) return;
      app.pushError(errText(err), "图片详情");
      activeAssetId.value = "";
      activeDetail.value = null;
    } finally {
      if (seq === detailSeq) {
        detailLoading.value = false;
      }
    }
  }

  function closeDetail(): void {
    detailSeq += 1;
    activeAssetId.value = "";
    activeDetail.value = null;
    detailLoading.value = false;
  }

  async function reloadActiveDetail(): Promise<void> {
    if (!activeAssetId.value) return;
    const id = activeAssetId.value;
    const res = await imageApi.getAssetDetail(
      id,
      selectedChatId.value || undefined,
      scope.value,
    );
    if (activeAssetId.value === id) {
      activeDetail.value = res;
    }
  }

  async function ingestUploadedImage(payload: {
    image_base64: string;
    chat_id?: string;
    user_statement?: string;
  }): Promise<boolean> {
    try {
      const res = await imageApi.ingestImage(
        {
          image_base64: payload.image_base64,
          source_kind: "manual",
          scope_type: payload.chat_id ? "chat" : "global",
          chat_id: payload.chat_id?.trim() || undefined,
          user_statement: payload.user_statement?.trim() || undefined,
        },
        scope.value,
      );
      app.pushSuccess(
        `图片已入库（状态：${res.embedding_status || "queued"}）`,
        "图片记忆",
      );
      await refreshAll(true);
      if (res.asset_id) {
        void ensurePreview(res.asset_id);
      }
      return true;
    } catch (err) {
      app.pushError(errText(err), "图片入库");
      return false;
    }
  }

  async function runVisualSearch(opts: {
    contentHash?: string;
    imageBase64?: string;
    label?: string;
  }): Promise<void> {
    searchOpen.value = true;
    if (opts.contentHash !== undefined) {
      searchHash.value = opts.contentHash;
      searchBase64.value = "";
    }
    if (opts.imageBase64 !== undefined) {
      searchBase64.value = opts.imageBase64;
      searchHash.value = "";
    }
    if (opts.label) {
      searchSourceLabel.value = opts.label;
    }
    if (!searchHash.value && !searchBase64.value) return;

    const seq = ++searchSeq;
    searching.value = true;
    try {
      const res = await imageApi.searchSimilar(
        {
          content_hash: searchHash.value || undefined,
          image_base64: searchBase64.value || undefined,
          chat_id: selectedChatId.value || undefined,
          similarity_threshold: similarityThreshold.value,
        },
        scope.value,
      );
      if (seq !== searchSeq) return;
      searchStatus.value = res.status || "";
      searchHits.value = Array.isArray(res.hits) ? res.hits : [];
      searchTimings.value = res.timings_ms ?? null;
      for (const hit of searchHits.value) {
        void ensurePreview(hit.asset_id);
      }
    } catch (err) {
      if (seq === searchSeq) {
        app.pushError(errText(err), "以图搜图");
        searchHits.value = [];
        searchTimings.value = null;
      }
    } finally {
      if (seq === searchSeq) {
        searching.value = false;
      }
    }
  }

  async function confirmObservation(obs: ImageObservation): Promise<void> {
    try {
      await imageApi.observeImage(
        {
          occurrence_id: obs.occurrence_id,
          text: obs.text,
          confirm_status: "confirmed",
          supersedes_id: resolveObservationId(obs) || undefined,
        },
        scope.value,
      );
      app.pushSuccess("已确认图片认知说明", "图片记忆");
      await Promise.all([reloadActiveDetail(), refreshAll(false)]);
    } catch (err) {
      app.pushError(errText(err), "确认说明");
    }
  }

  async function saveObservationText(params: {
    occurrenceId: string;
    text: string;
    supersedesId?: string;
  }): Promise<boolean> {
    if (!params.text.trim()) return false;
    try {
      await imageApi.observeImage(
        {
          occurrence_id: params.occurrenceId,
          text: params.text.trim(),
          confirm_status: "confirmed",
          supersedes_id: params.supersedesId || undefined,
        },
        scope.value,
      );
      app.pushSuccess(params.supersedesId ? "认知说明已修正" : "已新增认知说明", "图片记忆");
      await Promise.all([reloadActiveDetail(), refreshAll(false)]);
      return true;
    } catch (err) {
      app.pushError(errText(err), "编辑说明");
      return false;
    }
  }

  async function addMemoryLink(params: {
    occurrenceId: string;
    targetType: string;
    targetId: string;
    linkKind: string;
  }): Promise<boolean> {
    if (!params.targetId.trim()) return false;
    try {
      await imageApi.linkMemory(
        {
          occurrence_id: params.occurrenceId,
          target_type: params.targetType,
          target_id: params.targetId.trim(),
          link_kind: params.linkKind || "evidence",
        },
        scope.value,
      );
      app.pushSuccess("已建立记忆关联", "图片记忆");
      await Promise.all([reloadActiveDetail(), refreshAll(false)]);
      return true;
    } catch (err) {
      app.pushError(errText(err), "关联记忆");
      return false;
    }
  }

  async function removeMemoryLink(linkId: string): Promise<void> {
    try {
      await imageApi.unlinkMemory(linkId, scope.value);
      app.pushSuccess("已解除记忆关联", "图片记忆");
      await Promise.all([reloadActiveDetail(), refreshAll(false)]);
    } catch (err) {
      app.pushError(errText(err), "解除关联");
    }
  }

  async function removeOccurrence(occurrenceId: string): Promise<void> {
    const confirmed = await app.requestConfirmation({
      title: "删除图片出现记录",
      message: "确定删除该次图片出现记录？若图片不再被其他会话引用，底层图片资产也将一并释放。",
      confirmText: "确认删除",
      danger: true,
    });
    if (!confirmed) return;

    try {
      const res = await imageApi.deleteOccurrence(occurrenceId, scope.value);
      app.pushSuccess(
        res.asset_released ? "记录已删除，孤立图片资产已释放" : "出现记录已删除",
        "图片记忆",
      );
      if (res.asset_released) {
        closeDetail();
      } else {
        await reloadActiveDetail();
      }
      await refreshAll(false);
    } catch (err) {
      app.pushError(errText(err), "删除记录");
    }
  }

  async function loadJobs(): Promise<void> {
    if (isDisabled.value) {
      jobs.value = [];
      jobsTotal.value = 0;
      return;
    }
    const seq = ++jobsSeq;
    jobsLoading.value = true;
    try {
      const res = await imageApi.listJobs(
        { limit: 60, offset: 0, status: jobStatusFilter.value || undefined },
        scope.value,
      );
      if (seq !== jobsSeq) return;
      jobs.value = Array.isArray(res.items) ? res.items : [];
      jobsTotal.value = res.total ?? jobs.value.length;
    } catch (err) {
      if (seq === jobsSeq) {
        app.pushError(errText(err), "队列诊断");
      }
    } finally {
      if (seq === jobsSeq) {
        jobsLoading.value = false;
      }
    }
  }

  async function triggerProcessJobs(): Promise<void> {
    jobsActionBusy.value = true;
    try {
      const res = await imageApi.processJobs(scope.value);
      if ((res as { status?: string }).status === "unconfigured") {
        app.notify?.(
          (res as { message?: string }).message || "未配置图片嵌入模型，已跳过后台向量计算",
          "warn",
          "队列维护",
        );
      } else {
        app.pushSuccess(`已推进任务队列（处理 ${res.processed ?? 0} 项）`, "队列维护");
      }
      await Promise.all([loadJobs(), refreshAll(false)]);
    } catch (err) {
      app.pushError(errText(err), "推进队列");
    } finally {
      jobsActionBusy.value = false;
    }
  }

  async function triggerRetryFailedJobs(): Promise<void> {
    jobsActionBusy.value = true;
    try {
      const res = await imageApi.retryFailedJobs(scope.value);
      app.pushSuccess(`已重试失败任务（共 ${res.count ?? 0} 项）`, "队列维护");
      await Promise.all([loadJobs(), refreshAll(false)]);
    } catch (err) {
      app.pushError(errText(err), "重试任务");
    } finally {
      jobsActionBusy.value = false;
    }
  }

  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (next === prev) return;
      loadSeq += 1;
      searchSeq += 1;
      jobsSeq += 1;
      previewMap.value = new Map();
      previewFailed.value = new Set();
      selectedChatId.value = "";
      searchHits.value = [];
      searchTimings.value = null;
      searchSourceLabel.value = "";
      searchStatus.value = "";
      closeDetail();
      if (assets.value.length || statusInfo.value) {
        void refreshAll(true);
      }
    },
  );

  return {
    statusInfo,
    assets,
    chatStats,
    selectedChatId,
    page,
    loading,
    runtimeStatus,
    disabledInfo,
    isDisabled,
    embeddingConfigured,
    totalAssetCount,
    totalPages,
    activeAssetId,
    detailLoading,
    activeDetail,
    searchOpen,
    searching,
    similarityThreshold,
    searchHits,
    searchTimings,
    searchSourceLabel,
    searchHash,
    searchStatus,
    jobsModalOpen,
    jobsLoading,
    jobsActionBusy,
    jobStatusFilter,
    jobs,
    jobsTotal,
    previewFailed,
    refreshAll,
    ensurePreview,
    getPreviewUrl,
    setChatFilter,
    goToPage,
    openDetail,
    closeDetail,
    ingestUploadedImage,
    runVisualSearch,
    confirmObservation,
    saveObservationText,
    addMemoryLink,
    removeMemoryLink,
    removeOccurrence,
    loadJobs,
    triggerProcessJobs,
    triggerRetryFailedJobs,
  };
});
