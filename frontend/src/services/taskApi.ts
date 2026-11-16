// Import / Summary / Admin 导入任务 API 封装（统一随记忆作用域路由）。

import { api } from "./api";

export type ImportMode = "text" | "file" | "json" | "paragraph" | "relation";

export type KnowledgeType = "auto" | "narrative" | "factual" | "structured" | "mixed";

export type RawScanAlias = "raw" | "plugin_data";

/** 任务终态集合（native ImportTaskManager 与 async_tasks 两种状态机并集）。 */
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

export interface RawScanCreateRequest {
  alias: RawScanAlias;
  relative_path?: string;
  glob?: string;
  recursive?: boolean;
  knowledge_type?: KnowledgeType | string;
  source?: string;
  chat_log?: boolean;
  narrative_window_size?: number;
  narrative_overlap?: number;
  [k: string]: unknown;
}

export interface SummaryTaskCreateRequest {
  session_id?: string | null;
  source: string;
  messages: unknown[];
  context_length?: number;
}

export interface TaskCreateResult {
  task_id: string;
  status?: string;
  created_at?: number;
}

export interface ImportChunk {
  chunk_id?: string;
  index?: number;
  chunk_type?: string;
  status?: string;
  step?: string;
  retryable?: boolean;
  progress?: number;
  error?: string;
  failed_at?: string;
  content_preview?: string;
  updated_at?: number;
  [k: string]: unknown;
}

export interface ImportFileItem {
  file_id?: string;
  name?: string;
  source_kind?: string;
  input_mode?: string;
  status?: string;
  current_step?: string;
  progress?: number;
  total_chunks?: number;
  done_chunks?: number;
  failed_chunks?: number;
  cancelled_chunks?: number;
  error?: string;
  warnings?: string[];
  chunks?: ImportChunk[];
  [k: string]: unknown;
}

export interface TaskDetail {
  task_id: string;
  task_type?: string;
  source?: string;
  status?: string;
  current_step?: string;
  progress?: number;
  total_chunks?: number;
  done_chunks?: number;
  failed_chunks?: number;
  cancelled_chunks?: number;
  file_count?: number;
  payload?: Record<string, unknown>;
  params?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error_message?: string;
  error?: string;
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
  updated_at?: number;
  cancel_requested?: boolean;
  retry_parent_task_id?: string;
  files?: ImportFileItem[];
  [k: string]: unknown;
}

export interface ImportAdminListResponse {
  success?: boolean;
  error?: string;
  items: TaskDetail[];
  count?: number;
}

export interface ImportAdminTaskResponse {
  success?: boolean;
  error?: string;
  task: TaskDetail | null;
}

export interface ImportChunksResponse {
  success?: boolean;
  error?: string;
  task_id?: string;
  file_id?: string;
  offset?: number;
  limit?: number;
  total?: number;
  items: ImportChunk[];
}

export interface ImportPathAliasesResponse {
  success?: boolean;
  error?: string;
  path_aliases?: Record<string, string>;
}

async function callImportAdmin<T extends { success?: boolean; error?: string }>(
  action: string,
  payload: Record<string, unknown>,
  scope: string,
): Promise<T> {
  const res = await api.post<T>(
    "/v1/admin/memory_import_admin",
    { action, payload },
    { scope },
  );
  if (res && res.success === false) {
    throw new Error(res.error || `导入管理操作失败 (${action})`);
  }
  return res;
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

export async function createRawScanTask(
  req: RawScanCreateRequest,
  scope: string,
): Promise<TaskDetail> {
  const res = await callImportAdmin<ImportAdminTaskResponse>(
    "create_raw_scan",
    { ...req },
    scope,
  );
  if (!res.task) {
    throw new Error(res.error || "创建目录扫描任务失败");
  }
  return res.task;
}

export async function listImportAdminTasks(
  limit = 30,
  scope: string,
): Promise<TaskDetail[]> {
  const res = await callImportAdmin<ImportAdminListResponse>(
    "list",
    { limit },
    scope,
  );
  return Array.isArray(res.items) ? res.items : [];
}

export async function getImportAdminTask(
  taskId: string,
  includeChunks = false,
  scope: string,
): Promise<TaskDetail | null> {
  try {
    const res = await api.post<ImportAdminTaskResponse>(
      "/v1/admin/memory_import_admin",
      { action: "get", payload: { task_id: taskId, include_chunks: includeChunks } },
      { scope },
    );
    if (res && res.success && res.task) {
      return res.task;
    }
    return null;
  } catch {
    return null;
  }
}

export async function getImportTaskChunks(
  taskId: string,
  fileId: string,
  offset = 0,
  limit = 50,
  scope: string,
): Promise<ImportChunksResponse> {
  return callImportAdmin<ImportChunksResponse>(
    "get_chunks",
    { task_id: taskId, file_id: fileId, offset, limit },
    scope,
  );
}

export async function cancelImportAdminTask(
  taskId: string,
  scope: string,
): Promise<TaskDetail> {
  const res = await callImportAdmin<ImportAdminTaskResponse>(
    "cancel",
    { task_id: taskId },
    scope,
  );
  if (!res.task) {
    throw new Error(res.error || "取消任务失败");
  }
  return res.task;
}

export async function retryFailedImportTask(
  taskId: string,
  overrides: Record<string, unknown> = {},
  scope: string,
): Promise<TaskDetail> {
  const res = await callImportAdmin<ImportAdminTaskResponse>(
    "retry_failed",
    { task_id: taskId, overrides },
    scope,
  );
  if (!res.task) {
    throw new Error(res.error || "重试失败分块未生成新任务");
  }
  return res.task;
}

export async function fetchImportPathAliases(
  scope: string,
): Promise<Record<string, string>> {
  try {
    const res = await callImportAdmin<ImportPathAliasesResponse>(
      "path_aliases",
      {},
      scope,
    );
    return res.path_aliases || {};
  } catch {
    return {};
  }
}

/**
 * 查询导入任务详情：
 * 优先查询 memory_import_admin (get) 获取完整文件与分块进度（涵盖 create_raw_scan / retry_failed / 粘贴 / 上传任务），
 * 若不存在（如 async_tasks 队列中的 paragraph/relation/json 任务）则自动回退 GET /v1/import/tasks/{id}。
 */
export async function getImportTask(taskId: string, scope: string): Promise<TaskDetail> {
  const nativeTask = await getImportAdminTask(taskId, false, scope);
  if (nativeTask) return nativeTask;
  return api.get<TaskDetail>(`/v1/import/tasks/${encodeURIComponent(taskId)}`, {
    scope,
  });
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

export function getSummaryTask(taskId: string, scope: string): Promise<TaskDetail> {
  return api.get<TaskDetail>(`/v1/summary/tasks/${encodeURIComponent(taskId)}`, {
    scope,
  });
}

export function isTerminalStatus(status: string | undefined): boolean {
  return !!status && TERMINAL_TASK_STATUSES.has(status);
}
