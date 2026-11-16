import { defineStore } from "pinia";
import { computed, reactive, ref, watch } from "vue";
import {
  governanceApi,
  type FeedbackActionLog,
  type FeedbackTaskDetail,
  type FeedbackTaskSummary,
  type FuzzyPlanRecord,
  type FuzzyPreviewDetail,
  type FuzzyScope,
  type TuningIntensity,
  type TuningObjective,
  type TuningTaskDetail,
  type TuningTaskSummary,
} from "@/services/governanceApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { errText } from "@/utils/error";

export type CorrectionSubTab = "fuzzy" | "feedback" | "tuning";

function isActiveTuningStatus(status?: string): boolean {
  return status === "queued" || status === "running" || status === "cancel_requested" || status === "pending";
}

export const useGovernanceStore = defineStore("governance", () => {
  const graph = useGraphStore();
  const app = useAppStore();

  const scope = computed(() => graph.effectiveScope());
  const activeTab = ref<CorrectionSubTab>("fuzzy");

  let fuzzySeq = 0;
  let feedbackListSeq = 0;
  let feedbackDetailSeq = 0;
  let tuningSeq = 0;

  // ==================== 1. 自然语言模糊修正 ====================
  const fuzzyDisabled = ref(false);
  const fuzzyForm = reactive({
    requestText: "",
    targetScope: "person_profile" as FuzzyScope,
    personLookupMode: "keyword" as "keyword" | "id",
    personKeyword: "",
    personId: "",
    chatId: "",
    limit: 12,
    reason: "",
  });
  const fuzzyBusy = ref(false);
  const fuzzyListLoading = ref(false);
  const fuzzyStatusFilter = ref("");
  const fuzzyScopeFilter = ref("");
  const activePlan = ref<FuzzyPlanRecord | null>(null);
  const activePreview = ref<FuzzyPreviewDetail | null>(null);
  const fuzzyPlans = ref<FuzzyPlanRecord[]>([]);

  async function loadFuzzyPlans(): Promise<void> {
    const seq = ++fuzzySeq;
    const reqScope = scope.value;
    fuzzyListLoading.value = true;
    try {
      const res = await governanceApi.fuzzyList(
        {
          scope: fuzzyScopeFilter.value || undefined,
          status: fuzzyStatusFilter.value || undefined,
          limit: 40,
        },
        reqScope,
      );
      if (seq !== fuzzySeq || reqScope !== scope.value) return;
      if (res.success === false && res.error === "fuzzy_modify_disabled") {
        fuzzyDisabled.value = true;
        fuzzyPlans.value = [];
        return;
      }
      fuzzyDisabled.value = false;
      fuzzyPlans.value = Array.isArray(res.items) ? res.items : [];
    } catch (err) {
      if (seq !== fuzzySeq || reqScope !== scope.value) return;
      const msg = errText(err);
      if (msg.includes("fuzzy_modify_disabled")) {
        fuzzyDisabled.value = true;
        return;
      }
      app.pushError(msg, "模糊修正记录");
    } finally {
      if (seq === fuzzySeq) fuzzyListLoading.value = false;
    }
  }

  async function generateFuzzyPreview(): Promise<void> {
    if (!fuzzyForm.requestText.trim()) {
      app.pushError("请输入自然语言修正指令", "模糊修正");
      return;
    }
    const reqScope = scope.value;
    fuzzyBusy.value = true;
    try {
      const res = await governanceApi.fuzzyPreview(
        {
          request_text: fuzzyForm.requestText.trim(),
          scope: fuzzyForm.targetScope,
          ...(fuzzyForm.personLookupMode === "id" && fuzzyForm.personId.trim()
            ? { person_id: fuzzyForm.personId.trim() }
            : {}),
          ...(fuzzyForm.personLookupMode === "keyword" && fuzzyForm.personKeyword.trim()
            ? { person_keyword: fuzzyForm.personKeyword.trim() }
            : {}),
          ...(fuzzyForm.chatId.trim() ? { chat_id: fuzzyForm.chatId.trim() } : {}),
          limit: Math.max(1, Math.min(50, Number(fuzzyForm.limit) || 12)),
          ...(fuzzyForm.reason.trim() ? { reason: fuzzyForm.reason.trim() } : {}),
        },
        reqScope,
      );
      if (reqScope !== scope.value) return;
      if (res.success === false && res.error === "fuzzy_modify_disabled") {
        fuzzyDisabled.value = true;
        app.notify("模糊修正尚未在运行设置中启用", "warn", "模糊修正");
        return;
      }
      fuzzyDisabled.value = false;
      activePlan.value = res.plan ?? (res.plan_id ? { plan_id: res.plan_id, status: "previewed" } : null);
      activePreview.value = res.preview ?? res.plan?.preview ?? null;
      app.pushSuccess("修正计划预览已生成，核对影响后可执行", "模糊修正");
      await loadFuzzyPlans();
    } catch (err) {
      app.pushError(errText(err), "模糊修正预览");
    } finally {
      fuzzyBusy.value = false;
    }
  }

  async function inspectFuzzyPlan(planId: string): Promise<void> {
    const reqScope = scope.value;
    fuzzyBusy.value = true;
    try {
      const res = await governanceApi.fuzzyGet(planId, reqScope);
      if (reqScope !== scope.value) return;
      if (res.plan) {
        activePlan.value = res.plan;
        activePreview.value = res.plan.preview ?? null;
      }
    } catch (err) {
      app.pushError(errText(err), "查看修正计划");
    } finally {
      fuzzyBusy.value = false;
    }
  }

  async function executeFuzzyPlan(planId: string): Promise<void> {
    const confirmed = await app.requestConfirmation({
      title: "执行模糊修正计划",
      message: `即将对计划 ${planId} 执行写入与级联状态变更。执行后仍可按计划快照回滚，确定继续吗？`,
      confirmText: "执行计划",
      danger: false,
    });
    if (!confirmed) return;

    const reqScope = scope.value;
    fuzzyBusy.value = true;
    try {
      const res = await governanceApi.fuzzyExecute(
        planId,
        fuzzyForm.reason.trim() || undefined,
        reqScope,
      );
      if (reqScope !== scope.value) return;
      if (res.plan) {
        activePlan.value = res.plan;
        activePreview.value = res.plan.preview ?? activePreview.value;
      }
      app.pushSuccess(`修正计划 ${planId} 已执行`, "模糊修正");
      await loadFuzzyPlans();
    } catch (err) {
      app.pushError(errText(err), "执行修正计划");
    } finally {
      fuzzyBusy.value = false;
    }
  }

  async function rollbackFuzzyPlan(planId: string): Promise<void> {
    const confirmed = await app.requestConfirmation({
      title: "回滚模糊修正计划",
      message: `确定回滚计划 ${planId} 吗？系统将根据执行前快照恢复被改写的画像、记忆与关系状态。`,
      confirmText: "回滚计划",
      danger: true,
    });
    if (!confirmed) return;

    const reqScope = scope.value;
    fuzzyBusy.value = true;
    try {
      const res = await governanceApi.fuzzyRollback(planId, "webui_manual_rollback", reqScope);
      if (reqScope !== scope.value) return;
      if (res.plan) {
        activePlan.value = res.plan;
      }
      app.pushSuccess(`修正计划 ${planId} 已回滚`, "模糊修正");
      await loadFuzzyPlans();
    } catch (err) {
      app.pushError(errText(err), "回滚修正计划");
    } finally {
      fuzzyBusy.value = false;
    }
  }

  async function reconcileFuzzyPlans(): Promise<void> {
    const reqScope = scope.value;
    fuzzyBusy.value = true;
    try {
      const res = await governanceApi.fuzzyReconcile(reqScope);
      if (reqScope !== scope.value) return;
      app.pushSuccess(
        `已完成超时计划巡检（清理 ${res.stale_executing_count ?? 0} 条卡滞计划）`,
        "模糊修正",
      );
      await loadFuzzyPlans();
    } catch (err) {
      app.pushError(errText(err), "巡检修正计划");
    } finally {
      fuzzyBusy.value = false;
    }
  }

  // ==================== 2. 对话反馈审计 ====================
  const feedbackLoading = ref(false);
  const feedbackDetailLoading = ref(false);
  const feedbackActionBusy = ref(false);
  const feedbackFilters = reactive({
    status: "",
    rollbackStatus: "",
    query: "",
  });
  const feedbackItems = ref<FeedbackTaskSummary[]>([]);
  const selectedFeedback = ref<FeedbackTaskDetail | null>(null);
  const selectedFeedbackLogs = ref<FeedbackActionLog[]>([]);

  async function loadFeedbackList(): Promise<void> {
    const seq = ++feedbackListSeq;
    const reqScope = scope.value;
    feedbackLoading.value = true;
    try {
      const res = await governanceApi.feedbackList(
        {
          limit: 60,
          status: feedbackFilters.status || undefined,
          rollback_status: feedbackFilters.rollbackStatus || undefined,
          query: feedbackFilters.query.trim() || undefined,
        },
        reqScope,
      );
      if (seq !== feedbackListSeq || reqScope !== scope.value) return;
      feedbackItems.value = Array.isArray(res.items) ? res.items : [];
      if (feedbackItems.value.length && !selectedFeedback.value) {
        void selectFeedbackTask(feedbackItems.value[0].task_id);
      }
    } catch (err) {
      if (seq === feedbackListSeq && reqScope === scope.value) {
        app.pushError(errText(err), "反馈审计");
      }
    } finally {
      if (seq === feedbackListSeq) feedbackLoading.value = false;
    }
  }

  async function selectFeedbackTask(taskId: number): Promise<void> {
    const seq = ++feedbackDetailSeq;
    const reqScope = scope.value;
    feedbackDetailLoading.value = true;
    try {
      const [detailRes, logsRes] = await Promise.all([
        governanceApi.feedbackGet(taskId, reqScope),
        governanceApi.feedbackLogs(taskId, reqScope).catch(() => ({ items: [] as FeedbackActionLog[] })),
      ]);
      if (seq !== feedbackDetailSeq || reqScope !== scope.value) return;
      selectedFeedback.value = detailRes.task ?? null;
      selectedFeedbackLogs.value =
        Array.isArray(logsRes.items) && logsRes.items.length
          ? logsRes.items
          : (detailRes.task?.action_logs ?? []);
    } catch (err) {
      if (seq === feedbackDetailSeq && reqScope === scope.value) {
        app.pushError(errText(err), "反馈详情");
      }
    } finally {
      if (seq === feedbackDetailSeq) feedbackDetailLoading.value = false;
    }
  }

  async function rollbackFeedbackTask(taskId: number): Promise<void> {
    const confirmed = await app.requestConfirmation({
      title: `回退对话纠错 #${taskId}`,
      message:
        "确定回退该次自动纠错吗？被标记遗忘的关系、写入的新记忆及人物画像变更将按回滚摘要恢复。",
      confirmText: "回退纠错",
      danger: true,
    });
    if (!confirmed) return;

    const reqScope = scope.value;
    feedbackActionBusy.value = true;
    try {
      const res = await governanceApi.feedbackRollback(taskId, "WebUI 管理员手动回退", reqScope);
      if (reqScope !== scope.value) return;
      if (res.already_rolled_back) {
        app.notify(`任务 #${taskId} 此前已完成回退`, "info", "反馈审计");
      } else {
        app.pushSuccess(`已回退对话纠错 #${taskId}`, "反馈审计");
      }
      await Promise.all([loadFeedbackList(), selectFeedbackTask(taskId)]);
    } catch (err) {
      app.pushError(errText(err), "回退反馈纠错");
    } finally {
      feedbackActionBusy.value = false;
    }
  }

  async function reconcileFeedbackTasks(): Promise<void> {
    const reqScope = scope.value;
    feedbackActionBusy.value = true;
    try {
      await governanceApi.feedbackReconcile(reqScope);
      if (reqScope !== scope.value) return;
      app.pushSuccess("已完成对话反馈任务状态对账", "反馈审计");
      await loadFeedbackList();
    } catch (err) {
      app.pushError(errText(err), "对账反馈任务");
    } finally {
      feedbackActionBusy.value = false;
    }
  }

  // ==================== 3. 检索自动调优 ====================
  const tuningLoading = ref(false);
  const tuningBusy = ref(false);
  const tuningSettings = ref<Record<string, unknown> | null>(null);
  const currentProfile = ref<Record<string, unknown> | null>(null);
  const exportedToml = ref("");
  const profileViewMode = ref<"summary" | "toml">("summary");

  const createTuningForm = reactive({
    objective: "balanced" as TuningObjective,
    intensity: "standard" as TuningIntensity,
    sampleSize: 40,
    llmEnabled: false,
  });

  const tuningTasks = ref<TuningTaskSummary[]>([]);
  const selectedTuningTask = ref<TuningTaskDetail | null>(null);
  const tuningReportMd = ref("");
  const reportLoading = ref(false);

  let pollTimer: number | null = null;

  function stopTuningPoll(): void {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function syncTuningPoll(): void {
    const anyRunning =
      isActiveTuningStatus(selectedTuningTask.value?.status) ||
      tuningTasks.value.some((t) => isActiveTuningStatus(t.status));
    if (anyRunning && pollTimer === null) {
      pollTimer = window.setInterval(() => {
        void refreshTuningTasksSilent();
      }, 2800);
    } else if (!anyRunning && pollTimer !== null) {
      stopTuningPoll();
    }
  }

  async function loadTuningWorkspace(): Promise<void> {
    const seq = ++tuningSeq;
    const reqScope = scope.value;
    tuningLoading.value = true;
    try {
      const [settingsRes, tasksRes] = await Promise.all([
        governanceApi.tuningGetSettings(reqScope),
        governanceApi.tuningListTasks(30, reqScope),
      ]);
      if (seq !== tuningSeq || reqScope !== scope.value) return;
      tuningSettings.value = settingsRes.settings ?? null;
      currentProfile.value = settingsRes.profile ?? null;
      tuningTasks.value = Array.isArray(tasksRes.items) ? tasksRes.items : [];
      if (!currentProfile.value) {
        const prof = await governanceApi.tuningGetProfile(reqScope);
        if (seq !== tuningSeq || reqScope !== scope.value) return;
        currentProfile.value = prof.profile ?? null;
      }
      if (tuningTasks.value.length && !selectedTuningTask.value) {
        await selectTuningTask(tuningTasks.value[0].task_id);
      }
      syncTuningPoll();
    } catch (err) {
      if (seq === tuningSeq && reqScope === scope.value) {
        app.pushError(errText(err), "检索自动调优");
      }
    } finally {
      if (seq === tuningSeq) tuningLoading.value = false;
    }
  }

  async function refreshTuningTasksSilent(): Promise<void> {
    const reqScope = scope.value;
    try {
      const tasksRes = await governanceApi.tuningListTasks(30, reqScope);
      if (reqScope !== scope.value) return;
      tuningTasks.value = Array.isArray(tasksRes.items) ? tasksRes.items : [];
      if (selectedTuningTask.value?.task_id) {
        const detailRes = await governanceApi.tuningGetTask(
          selectedTuningTask.value.task_id,
          true,
          reqScope,
        );
        if (reqScope === scope.value && detailRes.task) {
          selectedTuningTask.value = detailRes.task;
        }
      }
      syncTuningPoll();
    } catch {
      stopTuningPoll();
    }
  }

  async function loadExportedToml(): Promise<void> {
    const reqScope = scope.value;
    try {
      const res = await governanceApi.tuningExportProfile(reqScope);
      if (reqScope === scope.value) {
        exportedToml.value = res.toml || "";
      }
    } catch (err) {
      app.pushError(errText(err), "导出 TOML 配置");
    }
  }

  async function selectTuningTask(taskId: string): Promise<void> {
    const reqScope = scope.value;
    tuningReportMd.value = "";
    try {
      const res = await governanceApi.tuningGetTask(taskId, true, reqScope);
      if (reqScope !== scope.value) return;
      selectedTuningTask.value = res.task ?? null;
      syncTuningPoll();
    } catch (err) {
      app.pushError(errText(err), "调优任务详情");
    }
  }

  async function startTuningTask(): Promise<void> {
    const reqScope = scope.value;
    tuningBusy.value = true;
    try {
      const res = await governanceApi.tuningCreateTask(
        {
          objective: createTuningForm.objective,
          intensity: createTuningForm.intensity,
          sample_size: Math.max(5, Math.min(500, Number(createTuningForm.sampleSize) || 40)),
          llm_enabled: createTuningForm.llmEnabled,
        },
        reqScope,
      );
      if (reqScope !== scope.value) return;
      app.pushSuccess(`调优任务已启动（ID：${res.task?.task_id || "-"}）`, "检索调优");
      const tasksRes = await governanceApi.tuningListTasks(30, reqScope);
      if (reqScope !== scope.value) return;
      tuningTasks.value = Array.isArray(tasksRes.items) ? tasksRes.items : [];
      if (res.task?.task_id) {
        await selectTuningTask(res.task.task_id);
      }
      syncTuningPoll();
    } catch (err) {
      app.pushError(errText(err), "创建调优任务");
    } finally {
      tuningBusy.value = false;
    }
  }

  async function cancelTuningTask(taskId: string): Promise<void> {
    const reqScope = scope.value;
    tuningBusy.value = true;
    try {
      const res = await governanceApi.tuningCancelTask(taskId, reqScope);
      if (reqScope !== scope.value) return;
      if (res.task) selectedTuningTask.value = res.task;
      app.notify(`已请求取消调优任务 ${taskId}`, "info", "检索调优");
      await refreshTuningTasksSilent();
    } catch (err) {
      app.pushError(errText(err), "取消调优任务");
    } finally {
      tuningBusy.value = false;
    }
  }

  async function applyBestTuning(taskId: string): Promise<void> {
    const confirmed = await app.requestConfirmation({
      title: "应用推荐检索参数",
      message:
        "系统将自动备份当前线上参数快照以备回滚，并将本次调优最优参数热更新至运行时。确认应用吗？",
      confirmText: "应用推荐参数",
      danger: false,
    });
    if (!confirmed) return;

    const reqScope = scope.value;
    tuningBusy.value = true;
    try {
      await governanceApi.tuningApplyBest(taskId, reqScope);
      if (reqScope !== scope.value) return;
      app.pushSuccess("推荐参数已应用并热重载", "检索调优");
      exportedToml.value = "";
      await Promise.all([loadTuningWorkspace(), selectTuningTask(taskId)]);
    } catch (err) {
      app.pushError(errText(err), "应用推荐参数");
    } finally {
      tuningBusy.value = false;
    }
  }

  async function rollbackCurrentProfile(): Promise<void> {
    const confirmed = await app.requestConfirmation({
      title: "回退检索调优参数",
      message: "确定将检索参数回退到上一次保存的快照并重建运行时吗？",
      confirmText: "回退参数",
      danger: true,
    });
    if (!confirmed) return;

    const reqScope = scope.value;
    tuningBusy.value = true;
    try {
      await governanceApi.tuningRollbackProfile(reqScope);
      if (reqScope !== scope.value) return;
      app.pushSuccess("检索参数已回退至上一快照", "检索调优");
      exportedToml.value = "";
      await loadTuningWorkspace();
    } catch (err) {
      app.pushError(errText(err), "回退检索参数");
    } finally {
      tuningBusy.value = false;
    }
  }

  async function fetchTaskMarkdownReport(taskId: string): Promise<void> {
    const reqScope = scope.value;
    reportLoading.value = true;
    try {
      const res = await governanceApi.tuningGetReport(taskId, "md", reqScope);
      if (reqScope === scope.value) {
        tuningReportMd.value = res.content || "（报告内容为空）";
      }
    } catch (err) {
      app.pushError(errText(err), "读取调优报告");
    } finally {
      reportLoading.value = false;
    }
  }

  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (next === prev) return;
      fuzzySeq += 1;
      feedbackListSeq += 1;
      feedbackDetailSeq += 1;
      tuningSeq += 1;
      stopTuningPoll();
      activePlan.value = null;
      activePreview.value = null;
      selectedFeedback.value = null;
      selectedFeedbackLogs.value = [];
      selectedTuningTask.value = null;
      tuningReportMd.value = "";
      exportedToml.value = "";

      if (activeTab.value === "fuzzy") void loadFuzzyPlans();
      else if (activeTab.value === "feedback") void loadFeedbackList();
      else void loadTuningWorkspace();
    },
  );

  return {
    activeTab,
    // Fuzzy
    fuzzyDisabled,
    fuzzyForm,
    fuzzyBusy,
    fuzzyListLoading,
    fuzzyStatusFilter,
    fuzzyScopeFilter,
    activePlan,
    activePreview,
    fuzzyPlans,
    loadFuzzyPlans,
    generateFuzzyPreview,
    inspectFuzzyPlan,
    executeFuzzyPlan,
    rollbackFuzzyPlan,
    reconcileFuzzyPlans,
    // Feedback
    feedbackLoading,
    feedbackDetailLoading,
    feedbackActionBusy,
    feedbackFilters,
    feedbackItems,
    selectedFeedback,
    selectedFeedbackLogs,
    loadFeedbackList,
    selectFeedbackTask,
    rollbackFeedbackTask,
    reconcileFeedbackTasks,
    // Tuning
    tuningLoading,
    tuningBusy,
    tuningSettings,
    currentProfile,
    exportedToml,
    profileViewMode,
    createTuningForm,
    tuningTasks,
    selectedTuningTask,
    tuningReportMd,
    reportLoading,
    stopTuningPoll,
    loadTuningWorkspace,
    loadExportedToml,
    selectTuningTask,
    startTuningTask,
    cancelTuningTask,
    applyBestTuning,
    rollbackCurrentProfile,
    fetchTaskMarkdownReport,
  };
});
