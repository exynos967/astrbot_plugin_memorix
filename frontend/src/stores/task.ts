import { defineStore } from "pinia";
import { ref, watch } from "vue";
import {
  cancelImportAdminTask,
  createImportTask as createImportApi,
  createRawScanTask,
  createSummaryTask as createSummaryApi,
  fetchImportPathAliases,
  getImportTask,
  getImportTaskChunks,
  getSummaryTask,
  isTerminalStatus,
  listImportAdminTasks,
  retryFailedImportTask,
  type ImportChunk,
  type ImportMode,
  type RawScanCreateRequest,
  type TaskCreateResult,
  type TaskDetail,
} from "@/services/taskApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";
import { uploadImport } from "@/services/api";

/**
 * Import / Summary 任务 store：
 * 1. 彻底隔离导入任务（currentTaskId / taskDetail / importTimer）与摘要任务（summaryTaskId / summaryResult / summaryTimer），杜绝交叉污染。
 * 2. setViewActive(true) 自动拉取近期导入任务列表，并在任务未达终态时恢复后台轮询。
 * 3. 完整集成 memory_import_admin：list / get / get_chunks / cancel / retry_failed / create_raw_scan。
 * 4. 支持多文件顺序上传进度追踪与作用域切换守卫。
 */
export const useTaskStore = defineStore("task", () => {
  // 导入任务状态
  const currentTaskId = ref("");
  const currentTaskType = ref<"import" | "summary" | "">("import");
  const taskDetail = ref<TaskDetail | null>(null);
  const importTasks = ref<TaskDetail[]>([]);
  const pathAliases = ref<Record<string, string>>({});
  const chunksByFile = ref<Record<string, { items: ImportChunk[]; total: number; loading: boolean }>>({});

  const creating = ref(false);
  const polling = ref(false);
  const loadingTasks = ref(false);
  const actionBusy = ref(false);
  const uploadProgressText = ref("");

  // 摘要任务独立状态
  const summaryTaskId = ref("");
  const summaryResult = ref<TaskDetail | null>(null);
  const creatingSummary = ref(false);
  const pollingSummary = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  let importTimer: number | null = null;
  let summaryTimer: number | null = null;
  let importRefreshSeq = 0;
  let summaryRefreshSeq = 0;
  let listSeq = 0;
  let importInflightKey: string | null = null;
  let summaryInflightKey: string | null = null;
  let viewActive = false;
  const POLL_INTERVAL = 2200;

  function stopPolling(): void {
    if (importTimer != null) {
      window.clearInterval(importTimer);
      importTimer = null;
    }
    polling.value = false;
  }

  function stopSummaryPolling(): void {
    if (summaryTimer != null) {
      window.clearInterval(summaryTimer);
      summaryTimer = null;
    }
    pollingSummary.value = false;
  }

  function stopAllPolling(): void {
    stopPolling();
    stopSummaryPolling();
  }

  async function loadPathAliases(): Promise<void> {
    const scope = graph.effectiveScope();
    const aliases = await fetchImportPathAliases(scope);
    if (scope === graph.effectiveScope()) {
      pathAliases.value = aliases;
    }
  }

  async function loadImportTasks(silent = false): Promise<void> {
    const seq = ++listSeq;
    const scope = graph.effectiveScope();
    loadingTasks.value = true;
    try {
      const items = await listImportAdminTasks(30, scope);
      if (seq !== listSeq || scope !== graph.effectiveScope()) return;
      importTasks.value = items;
      // 若当前尚未选中任务且列表存在活跃任务，自动挂载最新一条方便观察
      if (!currentTaskId.value && items.length > 0) {
        const first = items[0];
        if (first?.task_id) {
          currentTaskId.value = first.task_id;
          taskDetail.value = first;
          if (viewActive && !isTerminalStatus(first.status)) {
            startPolling();
          }
        }
      }
    } catch (err) {
      if (!silent && seq === listSeq) {
        app.pushError(errText(err), "导入任务列表");
      }
    } finally {
      if (seq === listSeq) loadingTasks.value = false;
    }
  }

  function syncTaskIntoList(detail: TaskDetail): void {
    const idx = importTasks.value.findIndex((t) => t.task_id === detail.task_id);
    if (idx >= 0) {
      const next = [...importTasks.value];
      next[idx] = { ...next[idx], ...detail };
      importTasks.value = next;
    }
  }

  async function refresh(silentIfEmpty = false): Promise<void> {
    const id = currentTaskId.value.trim();
    if (!id) {
      if (!silentIfEmpty) app.pushError("请先填写或选择一个导入任务 ID", "任务状态");
      return;
    }
    const scope = graph.effectiveScope();
    const requestKey = `${scope}::import::${id}`;
    if (importInflightKey === requestKey) return;
    importInflightKey = requestKey;
    const seq = ++importRefreshSeq;
    const isCurrent = () =>
      seq === importRefreshSeq &&
      id === currentTaskId.value.trim() &&
      scope === graph.effectiveScope();

    try {
      const data = await getImportTask(id, scope);
      if (!isCurrent()) return;
      taskDetail.value = data;
      syncTaskIntoList(data);
      if (isTerminalStatus(data.status)) {
        stopPolling();
        void loadImportTasks(true);
      }
    } catch (err) {
      if (isCurrent()) {
        app.pushError(errText(err), "任务状态");
        stopPolling();
      }
    } finally {
      if (importInflightKey === requestKey) importInflightKey = null;
    }
  }

  async function refreshSummary(silentIfEmpty = false): Promise<void> {
    const id = summaryTaskId.value.trim();
    if (!id) {
      if (!silentIfEmpty) app.pushError("请先填写摘要任务 ID", "摘要任务");
      return;
    }
    const scope = graph.effectiveScope();
    const requestKey = `${scope}::summary::${id}`;
    if (summaryInflightKey === requestKey) return;
    summaryInflightKey = requestKey;
    const seq = ++summaryRefreshSeq;
    const isCurrent = () =>
      seq === summaryRefreshSeq &&
      id === summaryTaskId.value.trim() &&
      scope === graph.effectiveScope();

    try {
      const data = await getSummaryTask(id, scope);
      if (!isCurrent()) return;
      summaryResult.value = data;
      if (isTerminalStatus(data.status)) {
        stopSummaryPolling();
      }
    } catch (err) {
      if (isCurrent()) {
        app.pushError(errText(err), "摘要任务");
        stopSummaryPolling();
      }
    } finally {
      if (summaryInflightKey === requestKey) summaryInflightKey = null;
    }
  }

  function startPolling(): void {
    stopPolling();
    if (!currentTaskId.value.trim() || !viewActive) return;
    polling.value = true;
    void refresh(true);
    importTimer = window.setInterval(() => void refresh(true), POLL_INTERVAL);
  }

  function startSummaryPolling(): void {
    stopSummaryPolling();
    if (!summaryTaskId.value.trim() || !viewActive) return;
    pollingSummary.value = true;
    void refreshSummary(true);
    summaryTimer = window.setInterval(() => void refreshSummary(true), POLL_INTERVAL);
  }

  function setViewActive(active: boolean): void {
    viewActive = active;
    if (!active) {
      stopAllPolling();
      return;
    }
    void loadPathAliases();
    void loadImportTasks(true);
    if (currentTaskId.value.trim() && (!taskDetail.value || !isTerminalStatus(taskDetail.value.status))) {
      startPolling();
    }
    if (summaryTaskId.value.trim() && (!summaryResult.value || !isTerminalStatus(summaryResult.value.status))) {
      startSummaryPolling();
    }
  }

  async function selectTask(taskId: string): Promise<void> {
    const id = taskId.trim();
    if (!id) return;
    currentTaskId.value = id;
    currentTaskType.value = "import";
    chunksByFile.value = {};
    await refresh(true);
    if (viewActive && taskDetail.value && !isTerminalStatus(taskDetail.value.status)) {
      startPolling();
    } else {
      stopPolling();
    }
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
      chunksByFile.value = {};
      app.pushSuccess(`导入任务已提交：${(data.task_id || "").slice(0, 10)}`, "导入任务");
      void loadImportTasks(true);
      if (viewActive) startPolling();
      return true;
    } catch (err) {
      app.pushError(errText(err), "导入任务");
      return false;
    } finally {
      creating.value = false;
    }
  }

  async function createRawScan(req: RawScanCreateRequest): Promise<boolean> {
    creating.value = true;
    const scope = graph.effectiveScope();
    try {
      const task = await createRawScanTask(req, scope);
      if (scope !== graph.effectiveScope()) return true;
      currentTaskId.value = task.task_id || "";
      currentTaskType.value = "import";
      taskDetail.value = task;
      chunksByFile.value = {};
      app.pushSuccess(
        `目录扫描任务已创建（共扫描到 ${task.file_count ?? task.files?.length ?? 0} 个文件）`,
        "目录扫描",
      );
      void loadImportTasks(true);
      if (viewActive && !isTerminalStatus(task.status)) startPolling();
      return true;
    } catch (err) {
      app.pushError(errText(err), "目录扫描");
      return false;
    } finally {
      creating.value = false;
    }
  }

  async function createUploads(
    files: File[],
    options: Record<string, unknown>,
  ): Promise<boolean> {
    if (!files.length) {
      app.pushError("请至少选择一个待上传文件", "文件导入");
      return false;
    }
    creating.value = true;
    uploadProgressText.value = "";
    const scope = graph.effectiveScope();
    const createdIds: string[] = [];

    try {
      for (let i = 0; i < files.length; i += 1) {
        const f = files[i];
        uploadProgressText.value = `正在上传 (${i + 1}/${files.length})：${f.name}`;
        const data = await uploadImport<TaskCreateResult>(f, options, scope);
        if (data?.task_id) {
          createdIds.push(data.task_id);
          logs.log(`文件「${f.name}」导入任务已创建：${data.task_id}`, "info", "文件导入");
        }
      }
      if (scope !== graph.effectiveScope()) return true;
      const latestId = createdIds[createdIds.length - 1] || "";
      if (latestId) {
        currentTaskId.value = latestId;
        currentTaskType.value = "import";
        chunksByFile.value = {};
      }
      app.pushSuccess(
        files.length === 1
          ? `文件「${files[0].name}」已提交导入`
          : `已成功提交 ${createdIds.length} 个文件的导入任务`,
        "文件导入",
      );
      await loadImportTasks(true);
      if (viewActive && latestId) startPolling();
      return true;
    } catch (error) {
      app.pushError(errText(error), "文件导入");
      if (createdIds.length > 0 && scope === graph.effectiveScope()) {
        currentTaskId.value = createdIds[createdIds.length - 1];
        void loadImportTasks(true);
        if (viewActive) startPolling();
      }
      return false;
    } finally {
      uploadProgressText.value = "";
      creating.value = false;
    }
  }

  async function createUpload(file: File, options: Record<string, unknown>): Promise<boolean> {
    return createUploads([file], options);
  }

  async function cancelTask(taskId?: string): Promise<boolean> {
    const id = (taskId || currentTaskId.value).trim();
    if (!id) return false;
    actionBusy.value = true;
    const scope = graph.effectiveScope();
    try {
      const updated = await cancelImportAdminTask(id, scope);
      if (scope !== graph.effectiveScope()) return true;
      if (id === currentTaskId.value.trim()) {
        taskDetail.value = updated;
        if (isTerminalStatus(updated.status)) stopPolling();
      }
      syncTaskIntoList(updated);
      app.pushSuccess(`已请求取消任务 ${id.slice(0, 10)}`, "导入任务");
      void loadImportTasks(true);
      return true;
    } catch (err) {
      app.pushError(errText(err), "取消任务");
      return false;
    } finally {
      actionBusy.value = false;
    }
  }

  async function retryFailed(taskId?: string): Promise<boolean> {
    const id = (taskId || currentTaskId.value).trim();
    if (!id) return false;
    actionBusy.value = true;
    const scope = graph.effectiveScope();
    try {
      const newTask = await retryFailedImportTask(id, {}, scope);
      if (scope !== graph.effectiveScope()) return true;
      currentTaskId.value = newTask.task_id;
      currentTaskType.value = "import";
      taskDetail.value = newTask;
      chunksByFile.value = {};
      app.pushSuccess(`已为失败分块创建重试任务：${newTask.task_id.slice(0, 10)}`, "失败重试");
      await loadImportTasks(true);
      if (viewActive && !isTerminalStatus(newTask.status)) startPolling();
      return true;
    } catch (err) {
      app.pushError(errText(err), "失败重试");
      return false;
    } finally {
      actionBusy.value = false;
    }
  }

  async function loadFileChunks(taskId: string, fileId: string): Promise<void> {
    if (!taskId || !fileId) return;
    const cacheKey = `${taskId}::${fileId}`;
    chunksByFile.value = {
      ...chunksByFile.value,
      [cacheKey]: { items: chunksByFile.value[cacheKey]?.items || [], total: 0, loading: true },
    };
    const scope = graph.effectiveScope();
    try {
      const res = await getImportTaskChunks(taskId, fileId, 0, 100, scope);
      if (scope !== graph.effectiveScope()) return;
      chunksByFile.value = {
        ...chunksByFile.value,
        [cacheKey]: {
          items: res.items || [],
          total: typeof res.total === "number" ? res.total : (res.items || []).length,
          loading: false,
        },
      };
    } catch (err) {
      app.pushError(errText(err), "分块明细");
      chunksByFile.value = {
        ...chunksByFile.value,
        [cacheKey]: { items: [], total: 0, loading: false },
      };
    }
  }

  async function createSummary(
    sessionId: string,
    source: string,
    messages: unknown[],
    contextLength = 50,
  ): Promise<boolean> {
    creatingSummary.value = true;
    const scope = graph.effectiveScope();
    try {
      const data = await createSummaryApi(
        {
          session_id: sessionId || null,
          source: source.trim() || "web_summary",
          messages,
          context_length: Math.max(1, Math.min(500, Number(contextLength) || 50)),
        },
        scope,
      );
      if (scope !== graph.effectiveScope()) return true;
      summaryTaskId.value = data.task_id || "";
      summaryResult.value = {
        task_id: data.task_id,
        status: data.status || "queued",
      };
      app.pushSuccess(`会话摘要任务已提交：${(data.task_id || "").slice(0, 10)}`, "摘要任务");
      if (viewActive) startSummaryPolling();
      return true;
    } catch (err) {
      app.pushError(errText(err), "摘要任务");
      return false;
    } finally {
      creatingSummary.value = false;
    }
  }

  watch(
    () => graph.effectiveScope(),
    () => {
      importRefreshSeq += 1;
      summaryRefreshSeq += 1;
      listSeq += 1;
      stopAllPolling();
      currentTaskId.value = "";
      taskDetail.value = null;
      importTasks.value = [];
      chunksByFile.value = {};
      summaryTaskId.value = "";
      summaryResult.value = null;
      if (viewActive) {
        void loadPathAliases();
        void loadImportTasks(true);
      }
    },
  );

  return {
    currentTaskId,
    currentTaskType,
    taskDetail,
    importTasks,
    pathAliases,
    chunksByFile,
    creating,
    polling,
    loadingTasks,
    actionBusy,
    uploadProgressText,
    summaryTaskId,
    summaryResult,
    creatingSummary,
    pollingSummary,
    refresh,
    refreshSummary,
    startPolling,
    stopPolling,
    startSummaryPolling,
    stopSummaryPolling,
    setViewActive,
    loadImportTasks,
    selectTask,
    createImport,
    createRawScan,
    createUpload,
    createUploads,
    cancelTask,
    retryFailed,
    loadFileChunks,
    createSummary,
  };
});
