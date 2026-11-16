// Episode API（typed 封装，统一经 effectiveScope 路由作用域）。
// 后端契约：
//   POST /v1/query/episode                 {query,time_from?,time_to?,person?,source?,top_k?,include_paragraphs}
//   GET  /v1/episode/{id}?include_paragraphs=true
//   POST /v1/admin/memory_episode_admin    {action: "status"|"rebuild"|"process_sources"|"process_pending"|"discard_migration_backfill", payload}
//   POST /v1/delete/paragraph              {paragraph_hash}

import { api } from "./api";

export interface EpisodeQueryRequest {
  query?: string;
  time_from?: string | null;
  time_to?: string | null;
  person?: string | null;
  source?: string | null;
  top_k?: number;
  include_paragraphs?: boolean;
}

export interface EpisodeParagraph {
  hash?: string;
  content?: string;
  type?: string;
  source?: string;
  created_at?: number;
  [k: string]: unknown;
}

export interface Episode {
  episode_id: string;
  source?: string | null;
  title?: string;
  summary?: string;
  content?: string;
  event_time_start?: number | null;
  event_time_end?: number | null;
  time_granularity?: string | null;
  time_confidence?: number;
  participants?: string[];
  keywords?: string[];
  evidence_ids?: string[];
  paragraph_count?: number;
  llm_confidence?: number;
  paragraphs?: EpisodeParagraph[];
  score?: number;
  created_at?: number;
  updated_at?: number;
  [k: string]: unknown;
}

export interface EpisodeQueryResult {
  query_type?: string;
  query?: string;
  count?: number;
  results: Episode[];
}

export interface EpisodeRebuildItem {
  source?: string;
  episode_count?: number;
  fallback_count?: number;
  group_count?: number;
  paragraph_count?: number;
  reused_group_count?: number;
  reused_episode_count?: number;
  [k: string]: unknown;
}

export interface EpisodeRebuildResult extends EpisodeRebuildItem {
  success?: boolean;
  error?: string;
  rebuilt?: number;
  unfinished?: number;
  count?: number;
  sources?: string[];
  items?: EpisodeRebuildItem[];
  failures?: Array<{ source?: string; error?: string }>;
}

export interface EpisodeLeaseStatus {
  success?: boolean;
  error?: string;
  counts?: {
    pending?: number;
    running?: number;
    failed?: number;
    succeeded?: number;
    done?: number;
    [k: string]: number | undefined;
  };
  failed?: Array<{
    source?: string;
    last_error?: string;
    error?: string;
    attempts?: number;
    updated_at?: number;
  }>;
  has_pending_paragraphs?: boolean;
  [k: string]: unknown;
}

export interface ParagraphDeleteResult {
  success?: boolean;
  paragraph_hash?: string;
  relation_prune_count?: number;
  deleted_vectors?: number;
  operation_id?: string;
  deleted?: {
    paragraph?: number;
    entity?: number;
    relation?: number;
  };
}

export type EpisodeAdminAction =
  | "status"
  | "rebuild"
  | "process_sources"
  | "process_pending"
  | "discard_migration_backfill";

export function queryEpisodes(
  req: EpisodeQueryRequest,
  scope: string,
): Promise<EpisodeQueryResult> {
  return api.post<EpisodeQueryResult>("/v1/query/episode", req, { scope });
}

export function fetchEpisode(
  episodeId: string,
  includeParagraphs: boolean,
  scope: string,
): Promise<Episode> {
  const q = includeParagraphs ? "?include_paragraphs=true" : "";
  return api.get<Episode>(`/v1/episode/${encodeURIComponent(episodeId)}${q}`, {
    scope,
  });
}

export async function episodeContractAdmin<T extends { success?: boolean; error?: string }>(
  action: EpisodeAdminAction,
  payload: Record<string, unknown>,
  scope: string,
): Promise<T> {
  const res = await api.post<T>(
    "/v1/admin/memory_episode_admin",
    { action, payload },
    { scope },
  );
  if (res && res.success === false && res.error) {
    throw new Error(res.error);
  }
  return res;
}

export function rebuildEpisode(
  source: string,
  scope: string,
): Promise<EpisodeRebuildResult> {
  const payload = source.trim() ? { source: source.trim() } : { all: true };
  return episodeContractAdmin<EpisodeRebuildResult>("rebuild", payload, scope);
}

export function deleteParagraphByHash(
  hash: string,
  scope: string,
): Promise<ParagraphDeleteResult> {
  return api.post<ParagraphDeleteResult>(
    "/v1/delete/paragraph",
    { paragraph_hash: hash },
    { scope },
  );
}
