import { defineStore } from "pinia";
import { ref, watch } from "vue";
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
import {
  runtimeContractAdmin,
  type RuntimeSelfCheckAdminResult,
  type VectorMaintenanceResult,
} from "@/services/memoryAdminApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";

/**
 * Memory store：记忆状态 + 回收站 + 关系操作 + 双池向量维护 + 跨面板同步信号（syncVersion）。
 * 监听 graph.effectiveScope()，切换作用域时清空旧数据并自动重载。
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

  // 双池向量运行时报告与维护操作结果
  const vectorReport = ref<RuntimeSelfCheckAdminResult["report"] | null>(null);
  const vectorMaintResult = ref<VectorMaintenanceResult | null>(null);
  const loadingVectors = ref(false);
  const vectorBusy = ref(false);

  // 跨面板同步修订号：RecyclePanel / DeleteOperationsPanel / MemoryStatusPanel 任意变更时自增
  const syncVersion = ref(0);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  let initialized = false;
  let reqSeq = 0;

  async function loadStatus(): Promise<void> {
    initialized = true;
    const seq = reqSeq;
    loadingStatus.value = true;
    try {
      const data = await fetchMemoryStatus(graph.effectiveScope());
      if (seq === reqSeq) status.value = data;
    } catch (err) {
      if (seq === reqSeq) app.pushError(errText(err), "loadMemoryStatus");
    } finally {
      if (seq === reqSeq) loadingStatus.value = false;
    }
  }

  async function loadRecycle(): Promise<void> {
    initialized = true;
    const seq = reqSeq;
    loadingRecycle.value = true;
    try {
      const data = await fetchRecycleBin(50, graph.effectiveScope());
      if (seq !== reqSeq) return;
      if (data?.error) {
        app.pushError(data.error, "recycleBin");
      }
      recycle.value = data.items || [];
    } catch (err) {
      if (seq === reqSeq) app.pushError(errText(err), "loadRecycle");
    } finally {
      if (seq === reqSeq) loadingRecycle.value = false;
    }
  }

  async function loadVectorStatus(): Promise<void> {
    initialized = true;
    const seq = reqSeq;
    loadingVectors.value = true;
    try {
      const res = await runtimeContractAdmin<RuntimeSelfCheckAdminResult>(
        "self_check",
        {},
        graph.effectiveScope(),
      );
      if (seq === reqSeq) {
        vectorReport.value = res?.report || null;
      }
    } catch (err) {
      if (seq === reqSeq) app.pushError(errText(err), "loadVectorStatus");
    } finally {
      if (seq === reqSeq) loadingVectors.value = false;
    }
  }

  /** 增量回填缺失段落向量（paragraph_backfill_once）。 */
  async function backfillMissingVectors(limit = 200): Promise<boolean> {
    if (vectorBusy.value) return false;
    vectorBusy.value = true;
    const scope = graph.effectiveScope();
    try {
      const res = await runtimeContractAdmin<VectorMaintenanceResult>(
        "paragraph_backfill_once",
        { limit },
        scope,
      );
      if (scope === graph.effectiveScope()) {
        vectorMaintResult.value = res;
      }
      logs.log(
        `向量增量回填完成：扫描缺失 ${res.processed ?? 0} 条，生成 ${res.generated ?? 0} 条`,
        "info",
      );
      app.pushSuccess?.(
        `增量回填完成：生成 ${res.generated ?? 0} / ${res.processed ?? 0} 条向量`,
        "vectorMaintenance",
      );
      await loadVectorStatus();
      return true;
    } catch (err) {
      app.pushError(errText(err), "paragraphBackfill");
      return false;
    } finally {
      vectorBusy.value = false;
    }
  }

  /** 重建全部双池向量（rebuild_all_vectors，需二次确认）。 */
  async function rebuildAllVectors(): Promise<boolean> {
    if (vectorBusy.value) return false;
    const confirmed = await app.requestConfirmation({
      title: "重建全部双池向量",
      message:
        "确定清空并重新编码当前作用域下的全部段落池与图谱池向量？若记忆规模较大可能耗时数十秒。",
      confirmText: "确认重建",
      danger: true,
    });
    if (!confirmed) return false;

    vectorBusy.value = true;
    const scope = graph.effectiveScope();
    try {
      const res = await runtimeContractAdmin<VectorMaintenanceResult>(
        "rebuild_all_vectors",
        {},
        scope,
      );
      if (scope === graph.effectiveScope()) {
        vectorMaintResult.value = res;
      }
      logs.log(
        `双池向量全量重建完成：共生成 ${res.generated ?? 0} / ${res.processed ?? 0} 条向量`,
        "info",
      );
      app.pushSuccess?.(
        `双池向量重建完成：共生成 ${res.generated ?? 0} 条向量`,
        "vectorMaintenance",
      );
      await loadVectorStatus();
      return true;
    } catch (err) {
      app.pushError(errText(err), "rebuildAllVectors");
      return false;
    } finally {
      vectorBusy.value = false;
    }
  }

  /** 关系操作（强化 / 限时保护 / 永久铭记 hours=0 / 冷冻）。 */
  async function runAction(
    action: MemoryAction,
    id: string,
    hours = 24,
  ): Promise<boolean> {
    const trimmed = id.trim();
    if (!trimmed) {
      app.pushError("请填写关系哈希、实体对或检索词", "runMemoryAction");
      return false;
    }
    actionBusy.value = true;
    try {
      const data = await runMemoryActionApi(action, trimmed, graph.effectiveScope(), hours);
      lastAction.value = data;
      lastActionId.value = trimmed;
      lastActionName.value = action;
      if (data?.success === false) {
        const msg = data.message || "未找到匹配的关系";
        app.pushError(msg, "runMemoryAction");
        logs.log(`记忆操作 [${action}]：${msg}`, "warn");
        return false;
      }
      const actionLabel =
        action === "reinforce"
          ? "强化"
          : action === "freeze"
            ? "冷冻"
            : hours <= 0
              ? "永久铭记"
              : `保护 ${hours}h`;
      logs.log(`记忆操作 [${actionLabel}] 完成：影响 ${data?.count ?? 1} 条关系`, "info");
      app.pushSuccess?.(`${actionLabel}成功`, "runMemoryAction");
      syncVersion.value += 1;
      await loadStatus();
      return true;
    } catch (err) {
      app.pushError(errText(err), "runMemoryAction");
      return false;
    } finally {
      actionBusy.value = false;
    }
  }

  /** 从回收站恢复（联动通知 DeleteOperationsPanel 与 MemoryStatusPanel）。 */
  async function restore(hash: string, type: string): Promise<boolean> {
    if (!hash) return false;
    actionBusy.value = true;
    try {
      const res = await restoreMemoryApi(hash, type, graph.effectiveScope());
      if (res?.success === false) {
        app.pushError(res.message || "恢复失败", "restoreMemory");
        return false;
      }
      logs.log(`记忆已恢复：${type} ${hash.slice(0, 12)}`, "info");
      app.pushSuccess?.("记忆已成功恢复", "restoreMemory");
      syncVersion.value += 1;
      await Promise.allSettled([loadRecycle(), loadStatus()]);
      return true;
    } catch (err) {
      app.pushError(errText(err), "restoreMemory");
      return false;
    } finally {
      actionBusy.value = false;
    }
  }

  /** 供 DeleteOperationsPanel 等子面板在删除/恢复/归档后触发全局记忆状态同步。 */
  async function notifyExternalMutation(): Promise<void> {
    syncVersion.value += 1;
    await Promise.allSettled([loadStatus(), loadRecycle()]);
  }

  // 监听作用域切换：清空旧作用域状态并刷新
  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (next === prev) return;
      reqSeq += 1;
      status.value = null;
      recycle.value = [];
      lastAction.value = null;
      lastActionId.value = "";
      lastActionName.value = "";
      vectorReport.value = null;
      vectorMaintResult.value = null;
      syncVersion.value += 1;
      if (initialized) {
        void Promise.allSettled([loadStatus(), loadRecycle(), loadVectorStatus()]);
      }
    },
  );

  return {
    status,
    recycle,
    lastAction,
    lastActionId,
    lastActionName,
    loadingStatus,
    loadingRecycle,
    actionBusy,
    vectorReport,
    vectorMaintResult,
    loadingVectors,
    vectorBusy,
    syncVersion,
    loadStatus,
    loadRecycle,
    loadVectorStatus,
    backfillMissingVectors,
    rebuildAllVectors,
    runAction,
    restore,
    notifyExternalMutation,
  };
});
