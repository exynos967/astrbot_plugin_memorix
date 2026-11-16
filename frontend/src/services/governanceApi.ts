// 纠错与调优服务层：对接 POST /v1/fuzzy_modify、POST /v1/feedback、POST /v1/tuning。
import { ApiError, api } from "@/services/api";

// ==================== 1. 自然语言模糊修正 ====================
export type FuzzyScope = "person_profile" | "memory";

export interface FuzzyCascadeCounts {
  relations?: number;
  relations_mark_inactive?: number;
  relations_mark_stale_evidence?: number;
  relations_skipped_protected?: number;
  entities?: number;
}

export interface FuzzyPreviewDetail {
  request_text?: string;
  scope?: FuzzyScope | string;
  person_id?: string;
  person_keyword?: string;
  chat_id?: string;
  confidence?: number;
  candidates?: Array<Record<string, unknown>>;
  operations?: Array<Record<string, unknown>>;
  cascade_preview?: {
    relations?: Array<Record<string, unknown>>;
    entities?: Array<Record<string, unknown>>;
    counts?: FuzzyCascadeCounts;
  };
  requires_confirmation?: boolean;
  confirm_threshold?: number;
  reason?: string;
}

export interface FuzzyPlanRecord {
  plan_id: string;
  scope?: string;
  status?: string;
  request_text?: string;
  person_id?: string;
  person_keyword?: string;
  chat_id?: string;
  confidence?: number;
  reason?: string;
  preview?: FuzzyPreviewDetail;
  execution?: Record<string, unknown>;
  rollback?: Record<string, unknown>;
  created_at?: number | string;
  updated_at?: number | string;
  executed_at?: number | string;
  rolled_back_at?: number | string;
}

export interface FuzzyActionResponse {
  success?: boolean;
  error?: string;
  plan_id?: string;
  plan?: FuzzyPlanRecord;
  preview?: FuzzyPreviewDetail;
  execution?: Record<string, unknown>;
  rollback?: Record<string, unknown>;
  items?: FuzzyPlanRecord[];
  count?: number;
  stale_executing_marked?: string[];
  stale_executing_count?: number;
}

// ==================== 2. 对话反馈纠错审计 ====================
export interface FeedbackTaskSummary {
  task_id: number;
  query_tool_id?: string;
  session_id?: string;
  query_text?: string;
  query_timestamp?: number | string;
  task_status?: string;
  decision?: string;
  decision_confidence?: number;
  feedback_message_count?: number;
  rollback_status?: string;
  affected_counts?: Record<string, number>;
  created_at?: number | string;
  updated_at?: number | string;
}

export interface FeedbackActionLog {
  id?: number | string;
  action_type?: string;
  action?: string;
  status?: string;
  target_type?: string;
  target_id?: string;
  detail?: Record<string, unknown> | string;
  message?: string;
  created_at?: number | string;
}

export interface FeedbackTaskDetail extends FeedbackTaskSummary {
  query_snapshot?: Record<string, unknown>;
  decision_payload?: Record<string, unknown>;
  rollback_plan_summary?: {
    forgotten_relations?: Array<Record<string, unknown>>;
    corrected_write?: Record<string, unknown> | Array<Record<string, unknown>> | null;
    stale_marks?: Array<Record<string, unknown>>;
    episode_sources?: string[];
    profile_person_ids?: string[];
    affected_counts?: Record<string, number>;
  };
  rollback_result?: Record<string, unknown>;
  rollback_error?: string;
  rollback_requested_by?: string;
  rollback_reason?: string;
  rollback_requested_at?: number | string;
  rolled_back_at?: number | string;
  action_logs?: FeedbackActionLog[];
}

// ==================== 3. 检索自动调优 ====================
export type TuningObjective = "precision_priority" | "balanced" | "recall_priority";
export type TuningIntensity = "quick" | "standard" | "deep";

export interface TuningTaskSummary {
  task_id: string;
  status: string;
  objective?: TuningObjective | string;
  intensity?: TuningIntensity | string;
  sample_size?: number;
  llm_enabled?: boolean;
  progress?: number;
  current_round?: number;
  total_rounds?: number;
  best_score?: number;
  baseline_score?: number;
  applied?: boolean;
  applied_at?: number | string;
  error?: string;
  created_at?: number | string;
  updated_at?: number | string;
}

export interface TuningTaskDetail extends TuningTaskSummary {
  baseline_metrics?: Record<string, number>;
  best_metrics?: Record<string, number>;
  holdout_validation?: {
    passed?: boolean;
    baseline?: Record<string, number>;
    best?: Record<string, number>;
    delta?: Record<string, number>;
    metrics?: Record<string, unknown>;
    reason?: string;
  };
  recommended_params?: Record<string, unknown>;
  best_params?: Record<string, unknown>;
  rounds?: Array<Record<string, unknown>>;
}

async function callFuzzy(
  action: string,
  payload: Record<string, unknown>,
  scope?: string,
): Promise<FuzzyActionResponse> {
  const res = await api.post<FuzzyActionResponse>("/v1/fuzzy_modify", { action, payload }, { scope });
  if (res && res.success === false) {
    if (res.error === "fuzzy_modify_disabled") {
      return res;
    }
    throw new ApiError(res.error || `模糊修正动作 ${action} 失败`, "/v1/fuzzy_modify", "POST");
  }
  return res;
}

async function callFeedback<T extends { success?: boolean; error?: string }>(
  action: string,
  payload: Record<string, unknown>,
  scope?: string,
): Promise<T> {
  const res = await api.post<T>("/v1/feedback", { action, payload }, { scope });
  if (res && res.success === false) {
    throw new ApiError(res.error || `反馈审计动作 ${action} 失败`, "/v1/feedback", "POST");
  }
  return res;
}

async function callTuning<T extends { success?: boolean; error?: string }>(
  action: string,
  payload: Record<string, unknown>,
  scope?: string,
): Promise<T> {
  const res = await api.post<T>("/v1/tuning", { action, payload }, { scope });
  if (res && res.success === false) {
    throw new ApiError(res.error || `自动调优动作 ${action} 失败`, "/v1/tuning", "POST");
  }
  return res;
}

export const governanceApi = {
  // 模糊修正
  fuzzyPreview: (
    payload: {
      request_text: string;
      scope: FuzzyScope;
      person_id?: string;
      person_keyword?: string;
      chat_id?: string;
      limit?: number;
      reason?: string;
    },
    scope?: string,
  ) => callFuzzy("preview", payload, scope),

  fuzzyExecute: (planId: string, reason?: string, scope?: string) =>
    callFuzzy("execute", { plan_id: planId, confirmed: true, ...(reason ? { reason } : {}) }, scope),

  fuzzyGet: (planId: string, scope?: string) => callFuzzy("get", { plan_id: planId }, scope),

  fuzzyList: (params: { scope?: string; status?: string; limit?: number }, scope?: string) =>
    callFuzzy(
      "list",
      {
        limit: params.limit ?? 40,
        ...(params.scope ? { scope: params.scope } : {}),
        ...(params.status ? { status: params.status } : {}),
      },
      scope,
    ),

  fuzzyRollback: (planId: string, reason?: string, scope?: string) =>
    callFuzzy("rollback", { plan_id: planId, ...(reason ? { reason } : {}) }, scope),

  fuzzyReconcile: (scope?: string) => callFuzzy("reconcile", {}, scope),

  // 对话反馈审计
  feedbackList: (
    params: { limit?: number; status?: string; rollback_status?: string; query?: string },
    scope?: string,
  ) =>
    callFeedback<{ success?: boolean; error?: string; items?: FeedbackTaskSummary[]; count?: number }>(
      "list",
      {
        limit: params.limit ?? 50,
        ...(params.status ? { status: params.status } : {}),
        ...(params.rollback_status ? { rollback_status: params.rollback_status } : {}),
        ...(params.query ? { query: params.query } : {}),
      },
      scope,
    ),

  feedbackGet: (taskId: number, scope?: string) =>
    callFeedback<{ success?: boolean; error?: string; task?: FeedbackTaskDetail }>(
      "get",
      { task_id: taskId },
      scope,
    ),

  feedbackLogs: (taskId: number, scope?: string) =>
    callFeedback<{ success?: boolean; error?: string; items?: FeedbackActionLog[]; task_id?: number }>(
      "logs",
      { task_id: taskId },
      scope,
    ),

  feedbackRollback: (taskId: number, reason?: string, scope?: string) =>
    callFeedback<{
      success?: boolean;
      error?: string;
      already_rolled_back?: boolean;
      task?: FeedbackTaskDetail;
      result?: Record<string, unknown>;
    }>(
      "rollback",
      {
        task_id: taskId,
        requested_by: "webui_admin",
        ...(reason ? { reason } : {}),
      },
      scope,
    ),

  feedbackReconcile: (scope?: string) =>
    callFeedback<{ success?: boolean; error?: string; result?: Record<string, unknown> }>(
      "reconcile",
      {},
      scope,
    ),

  // 检索自动调优
  tuningGetSettings: (scope?: string) =>
    callTuning<{
      success?: boolean;
      error?: string;
      settings?: Record<string, unknown>;
      profile?: Record<string, unknown>;
    }>("get_settings", {}, scope),

  tuningGetProfile: (scope?: string) =>
    callTuning<{ success?: boolean; error?: string; profile?: Record<string, unknown> }>(
      "get_profile",
      {},
      scope,
    ),

  tuningExportProfile: (scope?: string) =>
    callTuning<{ success?: boolean; error?: string; toml?: string }>("export_profile", {}, scope),

  tuningRollbackProfile: (scope?: string) =>
    callTuning<{
      success?: boolean;
      error?: string;
      rolled_back_to?: unknown;
      rolled_back_at?: number | string;
      runtime_rebuilt?: boolean;
      validation_passed?: boolean;
    }>("rollback_profile", {}, scope),

  tuningCreateTask: (
    payload: {
      objective: TuningObjective;
      intensity: TuningIntensity;
      sample_size: number;
      llm_enabled: boolean;
    },
    scope?: string,
  ) =>
    callTuning<{ success?: boolean; error?: string; task?: TuningTaskSummary }>(
      "create_task",
      payload,
      scope,
    ),

  tuningListTasks: (limit = 30, scope?: string) =>
    callTuning<{ success?: boolean; error?: string; items?: TuningTaskSummary[]; count?: number }>(
      "list_tasks",
      { limit },
      scope,
    ),

  tuningGetTask: (taskId: string, includeRounds = true, scope?: string) =>
    callTuning<{ success?: boolean; error?: string; task?: TuningTaskDetail }>(
      "get_task",
      { task_id: taskId, include_rounds: includeRounds },
      scope,
    ),

  tuningCancelTask: (taskId: string, scope?: string) =>
    callTuning<{ success?: boolean; error?: string; task?: TuningTaskDetail }>(
      "cancel",
      { task_id: taskId },
      scope,
    ),

  tuningApplyBest: (taskId: string, scope?: string) =>
    callTuning<{
      success?: boolean;
      error?: string;
      applied?: Record<string, unknown> | boolean;
      reason?: string;
      runtime_rebuilt?: boolean;
      validation_passed?: boolean;
    }>("apply_best", { task_id: taskId }, scope),

  tuningGetReport: (taskId: string, format: "md" | "json" = "md", scope?: string) =>
    callTuning<{ success?: boolean; error?: string; format?: string; content?: string; path?: string }>(
      "get_report",
      { task_id: taskId, format },
      scope,
    ),
};
