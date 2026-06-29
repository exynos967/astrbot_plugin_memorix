import { defineStore } from "pinia";
import { ref } from "vue";
import {
  createImportTask as createImportApi,
  createSummaryTask as createSummaryApi,
  getImportTask,
  getSummaryTask,
  isTerminalStatus,
  type ImportMode,
  type TaskDetail,
} from "@/services/taskApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err ?? "未知错误");
}

/**
 * Import/Summary 任务 store。
 *
 * 修复 legacy 缺陷：legacy 完全无任务轮询（仅手动点"刷新任务"），且无定时器清理。
 * 本 store 内建轮询：startPolling 后按 interval 周期查询任务详情，达到终态自动停止，
 * stopPolling 清理定时器。ImportView onBeforeUnmount 调 stopPolling，杜绝泄漏。
 *
 * scope：/v1/import|summary/tasks 经 bridge _scope 路由，统一传 effectiveScope。
 * currentTaskId 是全局唯一（与计划 useAppStore 一致），创建任务后回填并启动轮询。
 */
export const useTaskStore = defineStore("task", () => {
  const currentTaskId = ref("");
  const currentTaskType = ref<"import" | "summary" | "">("");
  const taskDetail = ref<TaskDetail | null>(null);
  const summaryResult = ref<TaskDetail | null>(null);
  const creating = ref(false);
  const polling = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  let timer: number | null = null;
  const POLL_INTERVAL = 2500;

  function stopPolling(): void {
    if (timer != null) {
      window.clearInterval(timer);
      timer = null;
    }
    polling.value = false;
  }

  async function refresh(): Promise<void> {
    const id = currentTaskId.value.trim();
    if (!id) {
      app.pushError("请填写 task id", "refreshTask");
      return;
    }
    try {
      const type = currentTaskType.value || "import";
      const data =
        type === "summary"
          ? await getSummaryTask(id, graph.effectiveScope())
          : await getImportTask(id, graph.effectiveScope());
      taskDetail.value = data;
      if (isTerminalStatus(data.status)) stopPolling();
    } catch (err) {
      app.pushError(errText(err), "refreshTask");
      stopPolling();
    }
  }

  function startPolling(): void {
    stopPolling();
    if (!currentTaskId.value) return;
    polling.value = true;
    void refresh();
    timer = window.setInterval(() => void refresh(), POLL_INTERVAL);
  }

  async function createImport(
    mode: ImportMode,
    payload: unknown,
    options: Record<string, unknown>,
  ): Promise<boolean> {
    creating.value = true;
    try {
      const data = await createImportApi({ mode, payload, options }, graph.effectiveScope());
      currentTaskId.value = data.task_id || "";
      currentTaskType.value = "import";
      logs.log(`导入任务已创建：${data.task_id || ""}`, "info");
      startPolling();
      return true;
    } catch (err) {
      app.pushError(errText(err), "createImportTask");
      return false;
    } finally {
      creating.value = false;
    }
  }

  async function createSummary(
    sessionId: string,
    source: string,
    messages: unknown[],
    contextLength = 50,
  ): Promise<boolean> {
    creating.value = true;
    try {
      const data = await createSummaryApi(
        {
          session_id: sessionId || null,
          source: source.trim() || "web_summary",
          messages,
          context_length: contextLength,
        },
        graph.effectiveScope(),
      );
      currentTaskId.value = data.task_id || "";
      currentTaskType.value = "summary";
      summaryResult.value = { task_id: data.task_id, status: data.status } as TaskDetail;
      logs.log(`摘要任务已创建：${data.task_id || ""}`, "info");
      startPolling();
      return true;
    } catch (err) {
      app.pushError(errText(err), "createSummaryTask");
      return false;
    } finally {
      creating.value = false;
    }
  }

  return {
    currentTaskId,
    currentTaskType,
    taskDetail,
    summaryResult,
    creating,
    polling,
    refresh,
    startPolling,
    stopPolling,
    createImport,
    createSummary,
  };
});
