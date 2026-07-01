// Import/Summary/Task API（typed 封装）。
// 后端契约（v1 端点，经 bridge _scope 路由到对应 scope 的 AppContext，统一传 scope）：
//   POST /v1/import/tasks          {mode, payload, options} → v1_router.py:293（返回 {task_id, status, created_at}）
//   GET  /v1/import/tasks/{id}     → v1_router.py:304（native ImportTaskRecord 或 async_tasks 行）
//   POST /v1/summary/tasks         {session_id?, source, messages, context_length} → v1_router.py:638
//   GET  /v1/summary/tasks/{id}    → v1_router.py:649
//
// 任务轮询：legacy 无轮询（仅手动刷新）。本 service 提供 getTask 统一入口，
// ImportView 层用 setInterval 轮询 + 终态停止 + onBeforeUnmount 清理（修 legacy 无清理隐患）。

import { api } from "./api";

export type ImportMode = "text" | "file" | "json" | "paragraph" | "relation";

/** 任务终态（停止轮询）。native 与 async 两种状态机的并集。 */
export const TERMINAL_TASK_STATUSES = new Set([
  "succeeded",
  "failed",
  "canceled",
  "cancelled",
  "completed",
  "completed_with_errors",
]);

export interface ImportTaskCreateRequest {
  mode: ImportMode;
  payload: unknown;
  options?: Record<string, unknown>;
}

export interface SummaryTaskCreateRequest {
  session_id?: string | null;
  source: string;
  messages: unknown[];
  context_length?: number;
}

/** 任务创建响应（import/summary 共用）。 */
export interface TaskCreateResult {
  task_id: string;
  status?: string;
  created_at?: number;
}

/** native ImportTaskRecord chunk。 */
export interface ImportChunk {
  chunk_id?: string;
  index?: number;
  chunk_type?: string;
  status?: string;
  step?: string;
  progress?: number;
  error?: string;
  content_preview?: string;
  [k: string]: unknown;
}

/** 任务详情（宽松并集，兼容 native ImportTaskRecord 与 async_tasks 行两种结构）。 */
export interface TaskDetail {
  task_id: string;
  task_type?: string;
  status?: string;
  current_step?: string;
  progress?: number;
  total_chunks?: number;
  done_chunks?: number;
  failed_chunks?: number;
  cancelled_chunks?: number;
  file_count?: number;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error_message?: string;
  error?: string;
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
  updated_at?: number;
  cancel_requested?: boolean;
  files?: Array<{
    file_id?: string;
    name?: string;
    status?: string;
    current_step?: string;
    progress?: number;
    total_chunks?: number;
    done_chunks?: number;
    failed_chunks?: number;
    chunks?: ImportChunk[];
    [k: string]: unknown;
  }>;
  [k: string]: unknown;
}

export function createImportTask(
  req: ImportTaskCreateRequest,
  scope: string,
): Promise<TaskCreateResult> {
  return api.post<TaskCreateResult>(
    "/v1/import/tasks",
    { mode: req.mode, payload: req.payload, options: req.options || {} },
    { scope },
  );
}

export function createSummaryTask(
  req: SummaryTaskCreateRequest,
  scope: string,
): Promise<TaskCreateResult> {
  return api.post<TaskCreateResult>(
    "/v1/summary/tasks",
    {
      session_id: req.session_id || null,
      source: req.source,
      messages: req.messages,
      context_length: req.context_length ?? 50,
    },
    { scope },
  );
}

/** 查询 import 任务详情。 */
export function getImportTask(taskId: string, scope: string): Promise<TaskDetail> {
  return api.get<TaskDetail>(`/v1/import/tasks/${encodeURIComponent(taskId)}`, {
    scope,
  });
}

/** 查询 summary 任务详情。 */
export function getSummaryTask(taskId: string, scope: string): Promise<TaskDetail> {
  return api.get<TaskDetail>(`/v1/summary/tasks/${encodeURIComponent(taskId)}`, {
    scope,
  });
}

/** 是否终态（用于轮询停止判断）。 */
export function isTerminalStatus(status: string | undefined): boolean {
  return !!status && TERMINAL_TASK_STATUSES.has(status);
}
