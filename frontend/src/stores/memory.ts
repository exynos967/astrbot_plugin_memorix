import { defineStore } from "pinia";
import { ref } from "vue";
import {
  fetchMemoryStatus,
  fetchRecycleBin,
  restoreMemory as restoreMemoryApi,
  runMemoryAction as runMemoryActionApi,
  type MemoryAction,
  type MemoryActionResult,
  type MemoryStatus,
  type RecycleItem,
} from "@/services/memoryApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";


/**
 * Memory store：记忆状态 + 回收站 + 关系操作结果。
 *
 * 修复 C5 类问题：所有请求统一经 graph.effectiveScope() 传 scope
 * （legacy loadMemoryStatus/runMemoryAction 均不带 scope）。
 * 错误显式进 useAppStore 总线（不静默吞错，修复 legacy 多处 catch 静默）。
 *
 * 注：runMemoryAction / restore 后联动刷新 graph 在 P8 GraphView 完成后才有意义，
 * P4 阶段仅刷新 status + recycle（graph store 的 loadGraph 在 P8 接入）。
 */
export const useMemoryStore = defineStore("memory", () => {
  const status = ref<MemoryStatus | null>(null);
  const recycle = ref<RecycleItem[]>([]);
  const lastAction = ref<MemoryActionResult | null>(null);
  const lastActionId = ref("");
  const lastActionName = ref<MemoryAction | "">("");
  const loadingStatus = ref(false);
  const loadingRecycle = ref(false);
  const actionBusy = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  async function loadStatus(): Promise<void> {
    loadingStatus.value = true;
    try {
      status.value = await fetchMemoryStatus(graph.effectiveScope());
    } catch (err) {
      app.pushError(errText(err), "loadMemoryStatus");
    } finally {
      loadingStatus.value = false;
    }
  }

  async function loadRecycle(): Promise<void> {
    loadingRecycle.value = true;
    try {
      const data = await fetchRecycleBin(50, graph.effectiveScope());
      recycle.value = data.items || [];
    } catch (err) {
      app.pushError(errText(err), "loadRecycle");
    } finally {
      loadingRecycle.value = false;
    }
  }

  /** 关系操作（强化/保护/冷冻）。空 id 返回 false 并提示。 */
  async function runAction(action: MemoryAction, id: string): Promise<boolean> {
    const trimmed = id.trim();
    if (!trimmed) {
      app.pushError("请填写关系 hash 或查询", "runMemoryAction");
      return false;
    }
    actionBusy.value = true;
    try {
      const data = await runMemoryActionApi(action, trimmed, graph.effectiveScope());
      lastAction.value = data;
      lastActionId.value = trimmed;
      lastActionName.value = action;
      logs.log(`记忆操作 ${action}：${data?.success === false ? "未找到" : "完成"}`, data?.success === false ? "warn" : "info");
      // 联动刷新 status（recycle 计数会变）
      await loadStatus();
      return true;
    } catch (err) {
      app.pushError(errText(err), "runMemoryAction");
      return false;
    } finally {
      actionBusy.value = false;
    }
  }

  /** 从回收站恢复。 */
  async function restore(hash: string, type: string): Promise<boolean> {
    if (!hash) return false;
    actionBusy.value = true;
    try {
      await restoreMemoryApi(hash, type, graph.effectiveScope());
      logs.log("记忆已恢复", "info");
      await Promise.allSettled([loadRecycle(), loadStatus()]);
      return true;
    } catch (err) {
      app.pushError(errText(err), "restoreMemory");
      return false;
    } finally {
      actionBusy.value = false;
    }
  }

  return {
    status,
    recycle,
    lastAction,
    lastActionId,
    lastActionName,
    loadingStatus,
    loadingRecycle,
    actionBusy,
    loadStatus,
    loadRecycle,
    runAction,
    restore,
  };
});
