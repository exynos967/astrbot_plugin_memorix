// 结构化管理契约 API：事实台账 (/api/facts & /v1/facts)、可恢复删除 (/v1/memory/delete-admin)、
// 人物别名 (/v1/person/aliases)、运行时双池向量维护 (/v1/admin/memory_runtime_admin) 与图片记忆统计 (/api/images/status)。

import { api } from "./api";
import type { RuntimeReport } from "./configApi";

export interface AdminResult {
  success?: boolean;
  error?: string;
}

export async function memoryAdmin<T extends AdminResult>(
  endpoint: "facts" | "person/aliases" | "memory/delete-admin",
  action: string,
  payload: Record<string, unknown>,
  scope: string,
): Promise<T> {
  const result = await api.post<T>(`/v1/${endpoint}`, { action, payload }, { scope });
  if (result && result.success === false) {
    throw new Error(result.error || "操作失败");
  }
  return result;
}

export async function runtimeContractAdmin<T extends AdminResult>(
  action: "self_check" | "rebuild_all_vectors" | "paragraph_backfill_once" | "save",
  payload: Record<string, unknown>,
  scope: string,
): Promise<T> {
  const result = await api.post<T>(
    "/v1/admin/memory_runtime_admin",
    { action, payload },
    { scope },
  );
  // self_check 在未配置 embedding 时可能返回 { success: false, report: {...} }，此时保留 report 供面板展示
  if (result && result.success === false && !("report" in result)) {
    throw new Error(result.error || "运行时管理操作失败");
  }
  return result;
}

export interface VectorPoolSnapshot {
  available?: boolean;
  dimension?: number;
  num_vectors?: number;
  has_data?: boolean;
}

export interface VectorPoolsInfo {
  configured_mode?: "dual" | "single" | string;
  effective_mode?: "dual" | "single" | string;
  ready?: boolean;
  single_pool?: VectorPoolSnapshot;
  paragraph_pool?: VectorPoolSnapshot;
  graph_pool?: VectorPoolSnapshot;
}

export interface RuntimeSelfCheckAdminResult extends AdminResult {
  report?: RuntimeReport & {
    configured_dimension?: number;
    requested_dimension?: number;
    vector_store_dimension?: number;
    detected_dimension?: number;
    encoded_dimension?: number;
    elapsed_ms?: number;
    vector_pools?: VectorPoolsInfo;
  };
}

export interface VectorMaintenanceResult extends AdminResult {
  processed?: number;
  generated?: number;
  paragraph_processed?: number;
  paragraph_generated?: number;
  entity_processed?: number;
  entity_generated?: number;
  relation_processed?: number;
  relation_generated?: number;
  embedding_degraded?: boolean;
  backfill_counts?: Record<string, number>;
  [key: string]: unknown;
}

export interface FactClaim {
  claim_id: string;
  scope_type: "person" | "chat" | string;
  scope_id: string;
  fact_key: string;
  value_text: string;
  value_normalized?: string;
  polarity: "positive" | "negative" | string;
  cardinality: "single" | "set" | string;
  conflict_group?: string;
  stability: "stable" | "temporal" | "uncertain" | string;
  profile_section: string;
  authority: "manual" | "direct_user" | "imported" | "summary_derived" | string;
  status: "active" | "retracted" | "superseded" | "conflicted" | string;
  confidence: number;
  valid_from?: number | null;
  valid_to?: number | null;
  first_observed_at?: number;
  last_confirmed_at?: number;
  created_at?: number;
  updated_at?: number;
}

export interface FactList extends AdminResult {
  items: FactClaim[];
  count?: number;
}

export interface FactMutationResult extends AdminResult {
  claim?: FactClaim;
  previous_claim_id?: string;
  replaced?: boolean;
  refresh_queued?: boolean;
}

export interface FactFilterParams {
  scope_type?: string;
  scope_id?: string;
  status?: string;
  limit?: number;
}

/**
 * 查询事实台账：
 * - 当 scope_type 与 scope_id 均明确指定时优先走 POST /v1/facts (action="list")；
 * - 当筛选「全部作用域类型」或未填 scope_id 时走 GET /api/facts，支持全表按更新时间浏览。
 */
export async function listFacts(
  params: FactFilterParams,
  scope: string,
): Promise<FactList> {
  const scopeType = (params.scope_type || "").trim();
  const scopeId = (params.scope_id || "").trim();
  const status = (params.status || "").trim();
  const limit = params.limit || 200;

  if ((scopeType === "person" || scopeType === "chat") && scopeId) {
    const statuses = status
      ? [status]
      : ["active", "conflicted", "superseded", "retracted"];
    return memoryAdmin<FactList>(
      "facts",
      "list",
      { scope_type: scopeType, scope_id: scopeId, statuses, limit },
      scope,
    );
  }

  const qs = new URLSearchParams();
  if (scopeType) qs.set("scope_type", scopeType);
  if (scopeId) qs.set("scope_id", scopeId);
  if (status) qs.set("status", status);
  qs.set("limit", String(limit));
  return api.get<FactList>(`/api/facts?${qs.toString()}`, { scope });
}

export function getFactClaim(
  claimId: string,
  scope: string,
): Promise<FactMutationResult> {
  return memoryAdmin<FactMutationResult>("facts", "get", { claim_id: claimId }, scope);
}

export function createFactClaim(
  payload: Record<string, unknown>,
  scope: string,
): Promise<FactMutationResult> {
  return memoryAdmin<FactMutationResult>("facts", "create", payload, scope);
}

export function updateFactClaim(
  claimId: string,
  payload: Record<string, unknown>,
  scope: string,
): Promise<FactMutationResult> {
  return memoryAdmin<FactMutationResult>(
    "facts",
    "update",
    { ...payload, claim_id: claimId },
    scope,
  );
}

export function retractFactClaim(
  claimId: string,
  scope: string,
  reason = "webui_manual_retract",
): Promise<FactMutationResult> {
  return memoryAdmin<FactMutationResult>(
    "facts",
    "retract",
    { claim_id: claimId, reason },
    scope,
  );
}

export function restoreFactClaim(
  claimId: string,
  scope: string,
  reason = "webui_manual_restore",
): Promise<FactMutationResult> {
  return memoryAdmin<FactMutationResult>(
    "facts",
    "restore",
    { claim_id: claimId, reason },
    scope,
  );
}

export interface AliasDetails extends AdminResult {
  manual_aliases: string[] | null;
  derived_aliases: string[];
  effective_aliases: string[];
}

export interface DeleteOperation {
  operation_id: string;
  mode: string;
  status: string;
  created_at: number;
  restored_at?: number | null;
  purged_at?: number | null;
  reason?: string;
  requested_by?: string;
  selector?: string[];
  summary?: { counts?: Record<string, number> };
}

export interface DeleteOperations extends AdminResult {
  items: DeleteOperation[];
  count?: number;
}

export interface DeletePreviewResult extends AdminResult {
  dry_run?: boolean;
  mode?: string;
  counts?: Record<string, number>;
  samples?: Record<string, string[]>;
}

export interface DeleteExecuteResult extends AdminResult {
  operation_id?: string;
  deleted?: Record<string, number>;
  deleted_count?: number;
  projection?: { pending?: number; processed?: number };
}

export interface DeletePurgeResult extends AdminResult {
  purged?: string[];
  count?: number;
  cutoff_time?: number;
}

/** Schema v31 图片记忆运行时状态（GET /api/images/status）。 */
export interface ImageMemoryStatus {
  success?: boolean;
  enabled: boolean;
  configured_enabled?: boolean;
  embedding_configured?: boolean;
  require_embedding?: boolean;
  paused_by_missing_embedding?: boolean;
  reason?: string;
  storage_bytes?: number;
  asset_count?: number;
  occurrence_count?: number;
  observation_count?: number;
  link_count?: number;
  ready_vector_count?: number;
  pending_job_count?: number;
  failed_job_count?: number;
  pending_description_count?: number;
  failed_description_count?: number;
  status?: string;
  message?: string;
}

export function fetchImageMemoryStatus(scope: string): Promise<ImageMemoryStatus> {
  return api.get<ImageMemoryStatus>("/api/images/status", { scope });
}
