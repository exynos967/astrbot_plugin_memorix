// Dashboard / 运行配置 / 运行时自检与双池存储落盘 API（统一随记忆作用域路由）。

import { api } from "./api";

/** /api/scopes 返回的单个 scope 项。 */
export interface ScopeOption {
  value: string;
  label: string;
}

/** /api/scopes 返回结构。 */
export interface ScopesPayload {
  current: string;
  scopes: ScopeOption[];
}

/** /v1/query/stats 返回结构。 */
export interface QueryStats {
  vector_store: { num_vectors: number; dimension?: number };
  graph_store: { num_nodes: number; num_edges: number };
  metadata_store: Record<string, number>;
  retriever?: unknown;
  sparse?: unknown;
}

/** v2.0 单个向量池健康快照。 */
export interface VectorPoolStatus {
  available?: boolean;
  dimension?: number | null;
  num_vectors?: number;
  has_data?: boolean;
}

/** v2.0 双向量池运行状态报告。 */
export interface VectorPoolsReport {
  configured_mode?: "single" | "dual" | string;
  effective_mode?: "single" | "dual" | string;
  ready?: boolean;
  single_pool?: VectorPoolStatus | null;
  paragraph_pool?: VectorPoolStatus | null;
  graph_pool?: VectorPoolStatus | null;
}

/** /v1/runtime/self_check 返回结构（含 v2.0 双向量池与多阶段维度诊断）。 */
export interface RuntimeReport {
  ok: boolean;
  message?: string;
  code?: string;
  embedding?: { dimension?: number; expected_dimension?: number; model?: string };
  dimension?: number;
  expected_dimension?: number;
  configured_dimension?: number | null;
  requested_dimension?: number | null;
  vector_store_dimension?: number | null;
  detected_dimension?: number | null;
  encoded_dimension?: number | null;
  elapsed_ms?: number;
  vector_pools?: VectorPoolsReport | null;
  model?: string;
  checked_at?: number;
  [key: string]: unknown;
}

/** /v1/dashboard/status.services.query.trend_buckets 单项。 */
export interface TrendBucket {
  start: number;
  end: number;
  total: number;
  types: Record<string, number>;
}

/** /v1/dashboard/status 返回结构。 */
export interface DashboardStatus {
  updated_at: number;
  stats: QueryStats;
  services: {
    graph: { status: string; nodes: number; relations: number; vectors: number };
    query: {
      status: string;
      recent_seconds: number;
      recent_count: number;
      recent_total_count: number;
      trend_seconds: number;
      trend_bucket_seconds: number;
      trend_total_count: number;
      trend_buckets: TrendBucket[];
    };
    episode: { status: string; count: number; queue: unknown };
    import: {
      status: string;
      latest_task: Record<string, unknown> | null;
      counts?: Record<string, number>;
    };
    person: { status: string; profile_count: number };
    runtime: { status: string; report: RuntimeReport | null };
  };
}

/** /api/config 返回结构（GET，脱敏只读）。 */
export interface ConfigPayload {
  auto_save_enabled: boolean;
  auto_save_interval: number;
  persist_supported?: boolean;
  config?: Record<string, unknown>;
}

/** PATCH /api/config/runtime 请求体（updates 为点分键→值）。 */
export interface RuntimeConfigPatch {
  updates: Record<string, unknown>;
  persist: boolean;
}

/** PATCH /api/config/runtime 返回结构。 */
export interface RuntimeConfigResult {
  success: boolean;
  persisted: boolean;
  persist_supported?: boolean;
  persist_message: string;
  auto_save_enabled: boolean;
  auto_save_interval: number;
  config?: Record<string, unknown>;
}

/** POST /api/config/auto_save 请求体。 */
export interface AutoSaveConfig {
  enabled: boolean;
}

/** POST /api/save 返回结构。 */
export interface ManualSaveResult {
  success: boolean;
  saved: string[];
}

export interface RuntimeAdminResponse {
  success?: boolean;
  error?: string;
  saved?: string[];
  report?: RuntimeReport;
  [k: string]: unknown;
}

export function fetchScopes(): Promise<ScopesPayload> {
  return api.get<ScopesPayload>("/api/scopes");
}

export function fetchStats(scope: string): Promise<QueryStats> {
  return api.get<QueryStats>("/v1/query/stats", { scope });
}

export function fetchDashboardStatus(scope: string): Promise<DashboardStatus> {
  return api.get<DashboardStatus>("/v1/dashboard/status", { scope });
}

export function fetchRuntimeSelfCheck(force: boolean, scope: string): Promise<RuntimeReport> {
  return api.post<RuntimeReport>(
    `/v1/runtime/self_check?force=${force ? "true" : "false"}`,
    {},
    { scope },
  );
}

export function fetchConfig(scope: string): Promise<ConfigPayload> {
  return api.get<ConfigPayload>("/api/config", { scope });
}

export function patchRuntimeConfig(patch: RuntimeConfigPatch, scope: string): Promise<RuntimeConfigResult> {
  return api.patch<RuntimeConfigResult>("/api/config/runtime", patch, { scope });
}

export function setAutoSave(enabled: boolean, scope: string): Promise<AutoSaveConfig & ConfigPayload> {
  return api.post<AutoSaveConfig & ConfigPayload>("/api/config/auto_save", { enabled }, { scope });
}

export function manualSave(scope: string): Promise<ManualSaveResult> {
  return api.post<ManualSaveResult>("/api/save", {}, { scope });
}

/**
 * 调用 /v1/admin/memory_runtime_admin（支持 save 双池向量+图谱完整落盘、self_check 等）。
 */
export async function callRuntimeAdmin(
  action: "save" | "self_check" | "refresh_self_check" | "set_auto_save" | "paragraph_backfill_once" | "rebuild_all_vectors",
  payload: Record<string, unknown> = {},
  scope: string,
): Promise<RuntimeAdminResponse> {
  const res = await api.post<RuntimeAdminResponse>(
    "/v1/admin/memory_runtime_admin",
    { action, payload },
    { scope },
  );
  if (res && res.success === false) {
    throw new Error(res.error || `运行时管理操作失败 (${action})`);
  }
  return res;
}
