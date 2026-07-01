import { defineStore } from "pinia";
import { reactive, ref, watch } from "vue";
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
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";


/**
 * Dashboard store：总览数据源（stats / dashboard status / runtime / autosave）。
 *
 * 修复 C2（refreshAll 竞态）：legacy refreshAll 并发 loadStats + loadGraph 写同一 metric DOM。
 * 新实现各请求写**独立 store 字段**（stats / status / runtime），无共享 DOM，Promise.allSettled
 * 错误隔离——单个失败不污染其他，错误进 useAppStore 总线由全局 toast 展示（不静默吞错）。
 *
 * 修复 C5（scope 统一）：所有 scoped 请求统一传 graph.effectiveScope()。
 * watch graph.currentScope → scope 切换联动刷新 stats + dashboard status（P8 下拉接入即生效）。
 */
export const useDashboardStore = defineStore("dashboard", () => {
  const stats = ref<QueryStats | null>(null);
  const status = ref<DashboardStatus | null>(null);
  const runtime = ref<RuntimeReport | null>(null);
  const config = ref<ConfigPayload | null>(null);
  /** 服务繁忙态（renderServiceRows 的 statusWithBusy 依据）。 */
  const busy = reactive<Record<string, boolean>>({});
  const loading = ref(false);

  const graph = useGraphStore();
  const app = useAppStore();
  const logs = useLogsStore();

  async function loadStats(): Promise<QueryStats | null> {
    try {
      const data = await fetchStats(graph.effectiveScope());
      stats.value = data;
      return data;
    } catch (err) {
      app.pushError(errText(err), "loadStats");
      return null;
    }
  }

  async function loadDashboardStatus(): Promise<DashboardStatus | null> {
    try {
      const data = await fetchDashboardStatus(graph.effectiveScope());
      status.value = data;
      return data;
    } catch (err) {
      app.pushError(errText(err), "loadDashboardStatus");
      return null;
    }
  }

  async function loadRuntime(force = false): Promise<RuntimeReport | null> {
    try {
      const data = await fetchRuntimeSelfCheck(force);
      runtime.value = data;
      logs.log(`运行时自检：${data.ok ? "通过" : "失败"}`, data.ok ? "info" : "warn");
      return data;
    } catch (err) {
      app.pushError(errText(err), "loadRuntime");
      return null;
    }
  }

  async function loadConfig(): Promise<ConfigPayload | null> {
    try {
      const data = await fetchConfig();
      config.value = data;
      return data;
    } catch (err) {
      app.pushError(errText(err), "loadConfig");
      return null;
    }
  }

  function setBusy(key: string, value: boolean): void {
    busy[key] = !!value;
  }

  /** 并发刷新所有 dashboard 数据（错误隔离，互不污染）。 */
  async function refreshAll(): Promise<void> {
    loading.value = true;
    try {
      await Promise.allSettled([loadConfig(), loadStats(), loadDashboardStatus(), loadRuntime(false)]);
    } finally {
      loading.value = false;
    }
  }

  // C5 联动：用户切换 scope（P8 下拉）→ graph.currentScope 变 → 重新拉取 scoped 数据。
  // 不监听 resolvedScope（仅 loadScopes 时设置一次，由 refreshAll 覆盖加载，避免重复）。
  watch(
    () => graph.currentScope,
    () => {
      void Promise.allSettled([loadStats(), loadDashboardStatus()]);
    },
  );

  return {
    stats,
    status,
    runtime,
    config,
    busy,
    loading,
    loadStats,
    loadDashboardStatus,
    loadRuntime,
    loadConfig,
    setBusy,
    refreshAll,
  };
});
