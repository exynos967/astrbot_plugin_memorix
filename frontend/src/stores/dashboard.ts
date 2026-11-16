import { defineStore } from "pinia";
import { reactive, ref, watch } from "vue";
import { api } from "@/services/api";
import {
  fetchConfig,
  fetchDashboardStatus,
  fetchRuntimeSelfCheck,
  fetchStats,
  type ConfigPayload,
  type DashboardStatus,
  type QueryStats,
  type RuntimeReport,
} from "@/services/configApi";
import {
  fetchImageMemoryStatus,
  type ImageMemoryStatus,
} from "@/services/memoryAdminApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";

export interface FeedbackSummaryState {
  enabled: boolean;
  status: string;
  count: number;
}

/**
 * Dashboard store：总览数据源（stats / dashboard status / runtime / config / imageStatus / feedbackSummary）。
 * 监听 graph.effectiveScope()，切换作用域时立即清空旧作用域残留并并发拉取最新数据。
 */
export const useDashboardStore = defineStore("dashboard", () => {
  const stats = ref<QueryStats | null>(null);
  const status = ref<DashboardStatus | null>(null);
  const runtime = ref<RuntimeReport | null>(null);
  const config = ref<ConfigPayload | null>(null);
  const imageStatus = ref<ImageMemoryStatus | null>(null);
  const feedbackSummary = ref<FeedbackSummaryState | null>(null);
  const busy = reactive<Record<string, boolean>>({});
  const loading = ref(false);

  const graph = useGraphStore();
  const app = useAppStore();
  const logs = useLogsStore();

  let initialized = false;
  let scopeReqSeq = 0;

  async function loadStats(seq = scopeReqSeq): Promise<QueryStats | null> {
    try {
      const data = await fetchStats(graph.effectiveScope());
      if (seq === scopeReqSeq) stats.value = data;
      return data;
    } catch (err) {
      if (seq === scopeReqSeq) app.pushError(errText(err), "loadStats");
      return null;
    }
  }

  async function loadDashboardStatus(seq = scopeReqSeq): Promise<DashboardStatus | null> {
    try {
      const data = await fetchDashboardStatus(graph.effectiveScope());
      if (seq === scopeReqSeq) status.value = data;
      return data;
    } catch (err) {
      if (seq === scopeReqSeq) app.pushError(errText(err), "loadDashboardStatus");
      return null;
    }
  }

  async function loadRuntime(force = false, seq = scopeReqSeq): Promise<RuntimeReport | null> {
    try {
      const data = await fetchRuntimeSelfCheck(force, graph.effectiveScope());
      if (seq === scopeReqSeq) {
        runtime.value = data;
        logs.log(`运行时自检：${data.ok ? "通过" : "异常"}`, data.ok ? "info" : "warn");
      }
      return data;
    } catch (err) {
      if (seq === scopeReqSeq) app.pushError(errText(err), "loadRuntime");
      return null;
    }
  }

  async function loadConfig(seq = scopeReqSeq): Promise<ConfigPayload | null> {
    try {
      const data = await fetchConfig(graph.effectiveScope());
      if (seq === scopeReqSeq) config.value = data;
      return data;
    } catch (err) {
      if (seq === scopeReqSeq) app.pushError(errText(err), "loadConfig");
      return null;
    }
  }

  async function loadImageStatus(seq = scopeReqSeq): Promise<ImageMemoryStatus | null> {
    try {
      const data = await fetchImageMemoryStatus(graph.effectiveScope());
      if (seq === scopeReqSeq) imageStatus.value = data;
      return data;
    } catch {
      // 图片记忆若在旧库或未初始化时不阻断主面板
      if (seq === scopeReqSeq) imageStatus.value = null;
      return null;
    }
  }

  async function loadFeedbackSummary(seq = scopeReqSeq): Promise<void> {
    try {
      const res = await api.post<{
        success?: boolean;
        error?: string;
        items?: Array<{ status?: string }>;
        count?: number;
      }>("/v1/feedback", { action: "list", payload: { limit: 20 } }, { scope: graph.effectiveScope() });
      if (seq !== scopeReqSeq) return;
      if (res?.success === false) {
        feedbackSummary.value = { enabled: false, status: "disabled", count: 0 };
        return;
      }
      const list = Array.isArray(res?.items) ? res.items : [];
      const hasRunning = list.some((it) => it?.status === "running" || it?.status === "pending");
      feedbackSummary.value = {
        enabled: true,
        status: hasRunning ? "running" : "ready",
        count: Number(res?.count ?? list.length),
      };
    } catch {
      if (seq === scopeReqSeq) {
        feedbackSummary.value = { enabled: false, status: "unknown", count: 0 };
      }
    }
  }

  function setBusy(key: string, value: boolean): void {
    busy[key] = !!value;
  }

  /** 并发刷新所有 dashboard 数据（错误隔离，互不污染）。 */
  async function refreshAll(forceRuntime = false): Promise<void> {
    initialized = true;
    const seq = ++scopeReqSeq;
    loading.value = true;
    try {
      await Promise.allSettled([
        loadConfig(seq),
        loadStats(seq),
        loadDashboardStatus(seq),
        loadRuntime(forceRuntime, seq),
        loadImageStatus(seq),
        loadFeedbackSummary(seq),
      ]);
    } finally {
      if (seq === scopeReqSeq) loading.value = false;
    }
  }

  // 监听 effectiveScope 切换：立即清空旧作用域快照并重载全部总览指标
  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (!initialized || next === prev) return;
      stats.value = null;
      status.value = null;
      runtime.value = null;
      imageStatus.value = null;
      feedbackSummary.value = null;
      void refreshAll(false);
    },
  );

  return {
    stats,
    status,
    runtime,
    config,
    imageStatus,
    feedbackSummary,
    busy,
    loading,
    loadStats,
    loadDashboardStatus,
    loadRuntime,
    loadConfig,
    loadImageStatus,
    setBusy,
    refreshAll,
  };
});
