// Episode API（typed 封装）。
// 后端契约（v1 端点，经 bridge _scope 路由到对应 scope 的 AppContext，故统一传 scope）：
//   POST /v1/query/episode      {query,time_from?,time_to?,person?,source?,top_k?,include_paragraphs} → v1_router.py:413
//   GET  /v1/episode/{id}?include_paragraphs=true → v1_router.py:497
//   POST /v1/episode/rebuild     {source} → v1_router.py:456（409 if running）
//   POST /v1/delete/paragraph    {paragraph_hash} → v1_router.py:513

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

export interface EpisodeRebuildResult {
  source?: string;
  episode_count?: number;
  fallback_count?: number;
  group_count?: number;
  paragraph_count?: number;
  [k: string]: unknown;
}

export interface ParagraphDeleteResult {
  success?: boolean;
  paragraph_hash?: string;
  relation_prune_count?: number;
  deleted_vectors?: number;
}

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

export function rebuildEpisode(
  source: string,
  scope: string,
): Promise<EpisodeRebuildResult> {
  return api.post<EpisodeRebuildResult>("/v1/episode/rebuild", { source }, { scope });
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
