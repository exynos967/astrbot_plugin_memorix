// 图片记忆服务层：对接 POST /v1/images 统一动作网关。
import { ApiError, api } from "@/services/api";

export type ImageRuntimeState =
  | "ready"
  | "pending"
  | "unavailable"
  | "unconfigured"
  | "exact_hash_only"
  | "failed"
  | "disabled";

export type ImageDisabledReason = "none" | "unconfigured_embedding" | "manual_off";

export interface ImageRuntimeStats {
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
  pending_unbound_description_count?: number;
}

export interface ImageIndexProgress {
  total: number;
  ready: number;
  remaining: number;
}

export interface ImageRecoverySummary {
  removed_files?: number;
  issues?: Array<{ storage_key?: string; error?: string }>;
}

export interface ImageStatusPayload {
  success?: boolean;
  error?: string;
  enabled?: boolean;
  configured_enabled?: boolean;
  embedding_configured?: boolean;
  require_embedding?: boolean;
  paused_by_missing_embedding?: boolean;
  reason?: string;
  status?: ImageRuntimeState | string;
  message?: string;
  fingerprint?: Record<string, unknown>;
  dimension?: number;
  generation?: number;
  stats?: ImageRuntimeStats;
  recovery?: ImageRecoverySummary;
  index_progress?: ImageIndexProgress;
}

export interface ImageAssetItem {
  asset_id: string;
  content_hash: string;
  storage_key?: string;
  mime_type?: string;
  byte_size?: number;
  width?: number;
  height?: number;
  status?: string;
  created_at?: number | string;
  updated_at?: number | string;
  occurrence_count?: number;
  latest_occurrence_at?: number | string;
}

export interface ImageListResponse extends ImageStatusPayload {
  items?: ImageAssetItem[];
  total?: number;
  count?: number;
}

export interface ImageChatStat {
  chat_id: string;
  asset_count: number;
}

export interface ImageOccurrence {
  occurrence_id: string;
  asset_id: string;
  external_ref?: string;
  source_kind?: string;
  scope_type?: string;
  chat_id?: string;
  message_id?: string;
  component_path?: string;
  occurred_at?: number | string;
  installation_id?: string;
  status?: string;
  created_at?: number | string;
}

export interface ImageObservation {
  observation_id?: string;
  id?: string;
  occurrence_id: string;
  text: string;
  source_kind?: string;
  confirm_status?: string;
  superseded_by?: string | null;
  supersedes_id?: string | null;
  evidence_json?: string | Record<string, unknown>;
  created_at?: number | string;
}

export interface ImageMemoryLink {
  link_id: string;
  occurrence_id: string;
  target_type: string;
  target_id: string;
  link_kind?: string;
  status?: string;
  memory?: {
    target_type?: string;
    target_id?: string;
    content?: string;
    summary?: string;
  } | null;
}

export interface ImageDetailResponse {
  success?: boolean;
  error?: string;
  asset?: ImageAssetItem;
  occurrences?: ImageOccurrence[];
  observations?: ImageObservation[];
  links?: ImageMemoryLink[];
}

export interface ImageReadResponse {
  success?: boolean;
  error?: string;
  asset_id?: string;
  mime_type?: string;
  data_url?: string;
  image_base64?: string;
  base64?: string;
  content_base64?: string;
}

export interface ImageIngestPayload {
  image_base64: string;
  source_kind?: "manual" | "chat";
  scope_type?: "global" | "chat";
  chat_id?: string;
  user_statement?: string;
}

export interface ImageIngestResponse {
  success?: boolean;
  error?: string;
  asset_id?: string;
  occurrence_id?: string;
  content_hash?: string;
  embedding_status?: string;
}

export interface ImageSearchHit {
  asset_id: string;
  match_kind: "exact_hash" | "visual_similar" | string;
  similarity: number;
  content_hash?: string;
  width?: number;
  height?: number;
  occurrences?: ImageOccurrence[];
  observations?: ImageObservation[];
  related_memories?: Array<{
    target_type?: string;
    target_id?: string;
    link_kind?: string;
    content?: string;
  }>;
}

export interface ImageSearchPayload {
  image_base64?: string;
  content_hash?: string;
  chat_id?: string;
  candidate_limit?: number;
  similarity_threshold?: number;
}

export interface ImageSearchResponse {
  success?: boolean;
  status?: string;
  error?: string;
  message?: string;
  embedding_configured?: boolean;
  query_asset_id?: string;
  hits?: ImageSearchHit[];
  timings_ms?: Record<string, number>;
  related_memory_count?: number;
}

export interface ImageQueueJob {
  kind: "embedding" | "description" | string;
  id: string;
  asset_id: string;
  status: string;
  attempt_count?: number;
  last_error?: string;
  updated_at?: number | string;
  lease_until?: number | string;
}

export interface ImageJobsResponse {
  success?: boolean;
  error?: string;
  items?: ImageQueueJob[];
  total?: number;
}

function isSoftDisabledResponse(res: { status?: string; error?: string }): boolean {
  const st = String(res.status || "").toLowerCase();
  const err = String(res.error || "");
  if (st === "disabled" || st === "unconfigured") return true;
  if (err === "image_memory_disabled") return true;
  return err.includes("未启用") || err.includes("禁用") || err.includes("已暂停");
}

async function callImages<T extends { success?: boolean; error?: string; status?: string }>(
  action: string,
  payload: Record<string, unknown>,
  scope?: string,
  allowSoftDisabled = false,
): Promise<T> {
  const res = await api.post<T>("/v1/images", { action, payload }, { scope });
  if (res && res.success === false) {
    if (allowSoftDisabled && isSoftDisabledResponse(res)) {
      return res;
    }
    throw new ApiError(res.error || `图片服务动作 ${action} 执行失败`, "/v1/images", "POST");
  }
  return res;
}

/**
 * 区分上游 407deaf90 两种未启用原因：
 * 1) unconfigured_embedding：未配置多模态图片嵌入模型，运行时自动暂停图片记忆（防识图报错）
 * 2) manual_off：用户在运行设置中手动关闭了 image_memory.enabled
 */
export function classifyImageMemoryDisabled(
  info:
    | {
        enabled?: boolean;
        configured_enabled?: boolean;
        embedding_configured?: boolean;
        require_embedding?: boolean;
        paused_by_missing_embedding?: boolean;
        reason?: string;
        status?: string;
        message?: string;
      }
    | null
    | undefined,
): {
  disabled: boolean;
  reason: ImageDisabledReason;
  title: string;
  detail: string;
  ctaLabel: string;
} {
  if (!info) {
    return {
      disabled: false,
      reason: "none",
      title: "",
      detail: "",
      ctaLabel: "配置图片嵌入模型",
    };
  }
  const isDisabled = info.enabled === false || info.status === "disabled";
  if (!isDisabled) {
    return {
      disabled: false,
      reason: "none",
      title: "",
      detail: "",
      ctaLabel: "配置图片嵌入模型",
    };
  }

  const msg = String(info.message || "").trim();
  const manualHint =
    info.reason === "manually_disabled" ||
    info.configured_enabled === false ||
    msg.includes("手动关闭") ||
    (msg.includes("配置中禁用") && info.embedding_configured !== false);

  const pausedForMissingEmbedding =
    info.paused_by_missing_embedding === true ||
    info.reason === "missing_image_embedding" ||
    (!manualHint &&
      (info.embedding_configured === false ||
        msg.includes("未配置") ||
        msg.includes("嵌入") ||
        msg.includes("防识图报错") ||
        msg.includes("暂停")) &&
      info.require_embedding !== false);

  if (pausedForMissingEmbedding) {
    return {
      disabled: true,
      reason: "unconfigured_embedding",
      title: "未配置多模态图片嵌入模型，图片记忆已暂停（防识图报错）",
      detail: "在设置中配置图片向量嵌入接口（image_memory.openapi）后，系统将自动恢复会话图片记忆与跨图相似检索。",
      ctaLabel: "配置图片嵌入模型",
    };
  }

  return {
    disabled: true,
    reason: "manual_off",
    title: "已在设置中关闭图片记忆",
    detail: "当前已在运行设置中手动关闭图片记忆模块，不会自动采集或检索会话图片。",
    ctaLabel: info.embedding_configured === false ? "配置图片嵌入模型" : "前往图片记忆设置",
  };
}

export function resolveObservationId(obs: ImageObservation): string {
  return String(obs.observation_id || obs.id || "");
}

export function extractDataUrlFromRead(res: ImageReadResponse): string {
  if (res.data_url && res.data_url.startsWith("data:")) {
    return res.data_url;
  }
  const raw = res.image_base64 || res.base64 || res.content_base64 || "";
  if (!raw) return "";
  if (raw.startsWith("data:")) return raw;
  const mime = res.mime_type || "image/png";
  return `data:${mime};base64,${raw}`;
}

export const imageApi = {
  getStatus: (scope?: string) => callImages<ImageStatusPayload>("status", {}, scope, true),

  listAssets: (params: { limit: number; offset: number; chat_id?: string }, scope?: string) =>
    callImages<ImageListResponse>(
      "list",
      {
        limit: params.limit,
        offset: params.offset,
        ...(params.chat_id ? { chat_id: params.chat_id } : {}),
      },
      scope,
      true,
    ),

  listChats: (scope?: string) =>
    callImages<{ success?: boolean; error?: string; items?: ImageChatStat[] }>("list_chats", {}, scope, true),

  getAssetDetail: (assetId: string, chatId?: string, scope?: string) =>
    callImages<ImageDetailResponse>(
      "get",
      { asset_id: assetId, ...(chatId ? { chat_id: chatId } : {}) },
      scope,
    ),

  readAssetPreview: (assetId: string, scope?: string) =>
    callImages<ImageReadResponse>("read", { asset_id: assetId }, scope),

  ingestImage: (payload: ImageIngestPayload, scope?: string) =>
    callImages<ImageIngestResponse>(
      "ingest",
      {
        image_base64: payload.image_base64,
        source_kind: payload.source_kind || "manual",
        scope_type: payload.scope_type || (payload.chat_id ? "chat" : "global"),
        ...(payload.chat_id ? { chat_id: payload.chat_id } : {}),
        ...(payload.user_statement ? { user_statement: payload.user_statement } : {}),
      },
      scope,
    ),

  searchSimilar: (payload: ImageSearchPayload, scope?: string) =>
    callImages<ImageSearchResponse>(
      "search",
      {
        ...(payload.image_base64 ? { image_base64: payload.image_base64 } : {}),
        ...(payload.content_hash ? { content_hash: payload.content_hash } : {}),
        ...(payload.chat_id ? { chat_id: payload.chat_id } : {}),
        candidate_limit: payload.candidate_limit ?? 24,
        similarity_threshold: payload.similarity_threshold ?? 0.72,
      },
      scope,
    ),

  observeImage: (
    payload: {
      occurrence_id: string;
      text: string;
      confirm_status?: string;
      supersedes_id?: string;
    },
    scope?: string,
  ) =>
    callImages<{ success?: boolean; error?: string; observation?: ImageObservation }>(
      "observe",
      {
        occurrence_id: payload.occurrence_id,
        text: payload.text,
        source_kind: "manual",
        confirm_status: payload.confirm_status || "confirmed",
        ...(payload.supersedes_id ? { supersedes_id: payload.supersedes_id } : {}),
      },
      scope,
    ),

  linkMemory: (
    payload: {
      occurrence_id: string;
      target_type: "paragraph" | "entity" | "relation" | "episode" | string;
      target_id: string;
      link_kind: string;
    },
    scope?: string,
  ) =>
    callImages<{ success?: boolean; error?: string; link?: ImageMemoryLink }>("link", payload, scope),

  unlinkMemory: (linkId: string, scope?: string) =>
    callImages<{ success?: boolean; error?: string; invalidated?: number }>(
      "unlink",
      { link_id: linkId },
      scope,
    ),

  deleteOccurrence: (occurrenceId: string, scope?: string) =>
    callImages<{ success?: boolean; error?: string; asset_released?: boolean }>(
      "delete_occurrence",
      { occurrence_id: occurrenceId },
      scope,
    ),

  listJobs: (params: { limit?: number; offset?: number; status?: string }, scope?: string) =>
    callImages<ImageJobsResponse>(
      "jobs",
      {
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
        ...(params.status ? { status: params.status } : {}),
      },
      scope,
    ),

  processJobs: (scope?: string) =>
    callImages<{ success?: boolean; error?: string; claimed?: number; processed?: number }>(
      "process_jobs",
      {},
      scope,
    ),

  retryFailedJobs: (scope?: string) =>
    callImages<{ success?: boolean; error?: string; count?: number; counts?: Record<string, number> }>(
      "retry_failed_jobs",
      {},
      scope,
    ),
};
