import { defineStore } from "pinia";
import { ref, watch } from "vue";
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
import { errText } from "@/utils/error";
import { uploadImport } from "@/services/api";
import type { TaskCreateResult } from "@/services/taskApi";


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
  let refreshSeq = 0;
  let inflightKey: string | null = null;
  /** 视图活跃标志：仅 ImportView 挂载期间为 true。防止创建请求 in-flight 时离开视图，
   *  await 返回后仍 startPolling 新建定时器导致泄漏（stopPolling 在 unmount 已执行，无可清）。 */
  let viewActive = false;
  const POLL_INTERVAL = 2500;

  /** ImportView onMounted/onBeforeUnmount 调用，标记视图是否仍在查看任务。 */
  function setViewActive(active: boolean): void {
    viewActive = active;
    if (!active) stopPolling();
  }

  function stopPolling(): void {
    if (timer != null) {
      window.clearInterval(timer);
      timer = null;
    }
    polling.value = false;
  }

  async function refresh(): Promise<void> {
    // in-flight 守卫：单次 refresh > POLL_INTERVAL 时跳过后续 tick，避免并发覆盖。
    const id = currentTaskId.value.trim();
    if (!id) {
      app.pushError("请填写 task id", "refreshTask");
      return;
    }
    const scope = graph.effectiveScope();
    const type = currentTaskType.value || "import";
    const requestKey = JSON.stringify([scope, type, id]);
    if (inflightKey === requestKey) return;
    inflightKey = requestKey;
    const seq = ++refreshSeq;
    const isCurrent = () => seq === refreshSeq && id === currentTaskId.value.trim() &&
      type === (currentTaskType.value || "import") && scope === graph.effectiveScope();
    try {
      const data =
        type === "summary"
          ? await getSummaryTask(id, scope)
          : await getImportTask(id, scope);
      if (!isCurrent()) return;
      taskDetail.value = data;
      // summaryResult 随轮询同步更新：原仅创建瞬间写桩值，SummaryTaskPanel 展示永不刷新。
      if (type === "summary") summaryResult.value = data;
      if (isTerminalStatus(data.status)) stopPolling();
    } catch (err) {
      if (isCurrent()) { app.pushError(errText(err), "refreshTask"); stopPolling(); }
    } finally {
      if (inflightKey === requestKey) inflightKey = null;
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
    const scope = graph.effectiveScope();
    try {
      const data = await createImportApi({ mode, payload, options }, scope);
      if (scope !== graph.effectiveScope()) return true;
      currentTaskId.value = data.task_id || "";
      currentTaskType.value = "import";
      logs.log(`导入任务已创建：${data.task_id || ""}`, "info");
      // 仅视图仍活跃时启动轮询，避免离开后创建 in-flight 完成导致定时器泄漏。
      if (viewActive) startPolling();
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
    const scope = graph.effectiveScope();
    try {
      const data = await createSummaryApi(
        {
          session_id: sessionId || null,
          source: source.trim() || "web_summary",
          messages,
          context_length: contextLength,
        },
        scope,
      );
      if (scope !== graph.effectiveScope()) return true;
      currentTaskId.value = data.task_id || "";
      currentTaskType.value = "summary";
      summaryResult.value = { task_id: data.task_id, status: data.status } as TaskDetail;
      logs.log(`摘要任务已创建：${data.task_id || ""}`, "info");
      if (viewActive) startPolling();
      return true;
    } catch (err) {
      app.pushError(errText(err), "createSummaryTask");
      return false;
    } finally {
      creating.value = false;
    }
  }

  async function createUpload(file: File, options: Record<string, unknown>): Promise<boolean> {
    creating.value = true;
    const scope = graph.effectiveScope();
    try {
      const data = await uploadImport<TaskCreateResult>(file, options, scope);
      logs.log(`文件导入任务已创建：${data.task_id}`, "info");
      if (scope !== graph.effectiveScope()) return true;
      currentTaskId.value = data.task_id;
      currentTaskType.value = "import";
      if (viewActive) startPolling();
      return true;
    } catch (error) {
      app.pushError(errText(error), "uploadImport");
      return false;
    } finally { creating.value = false; }
  }

  watch(() => graph.effectiveScope(), () => {
    ++refreshSeq;
    stopPolling(); currentTaskId.value = ""; currentTaskType.value = "";
    taskDetail.value = null; summaryResult.value = null;
  });

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
    setViewActive,
    createImport,
    createUpload,
    createSummary,
  };
});
