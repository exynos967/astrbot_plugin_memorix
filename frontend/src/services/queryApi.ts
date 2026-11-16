// Query API（typed 封装，统一经 effectiveScope 路由作用域）。
// 后端契约：
//   POST /v1/query/auto       {query, top_k, source, strict_source}
//   POST /v1/query/aggregate  {query, top_k, time_from, time_to, person, source, mix, mix_top_k}
//   POST /v1/query/search     {query, top_k, source?, strict_source?}
//   POST /v1/query/time       {query, top_k, time_from, time_to, person, source}
//   POST /v1/query/entity     {entity_name}
//   POST /v1/query/relation   {subject, predicate, object}
//   POST /v1/query/episode    （复用 episodeApi.queryEpisodes）

import { api } from "./api";

export type QueryMode =
  | "auto"
  | "aggregate"
  | "search"
  | "time"
  | "episode"
  | "relation"
  | "entity";

export interface QueryRequest {
  query?: string;
  top_k?: number;
  time_from?: string | null;
  time_to?: string | null;
  person?: string | null;
  source?: string | null;
  strict_source?: boolean;
  mix?: boolean;
  mix_top_k?: number;
  entity_name?: string;
  subject?: string;
  predicate?: string;
  object?: string;
  include_paragraphs?: boolean;
}

export interface QueryResultItem {
  [k: string]: unknown;
}

export interface AggregateBranchInfo {
  name?: string;
  status?: string;
  success?: boolean;
  skipped?: boolean;
  skip_reason?: string;
  error?: string;
  count?: number;
  elapsed_ms?: number;
  results?: QueryResultItem[];
}

export interface QueryResult {
  success?: boolean;
  query_type?: string;
  query?: string;
  count?: number;
  elapsed_ms?: number;
  route_reason?: string;
  matched_time?: string;
  time_from?: string | number | null;
  time_to?: string | number | null;
  filtered?: boolean;
  entity_name?: string;
  mixed_results?: QueryResultItem[];
  results?: QueryResultItem[];
  relations?: QueryResultItem[];
  paragraphs?: QueryResultItem[];
  neighbors?: string[];
  branches?: Record<string, AggregateBranchInfo>;
  summary?: Record<string, { status?: string; count?: number; reason?: string; error?: string }>;
  errors?: Array<{ branch?: string; error?: string }>;
  [k: string]: unknown;
}

export function runAuto(
  req: QueryRequest,
  scope: string,
): Promise<QueryResult> {
  return api.post<QueryResult>(
    "/v1/query/auto",
    {
      query: req.query ?? "",
      top_k: req.top_k,
      source: req.source ?? null,
      strict_source: !!req.strict_source,
    },
    { scope },
  );
}

export function runAggregate(
  req: QueryRequest,
  scope: string,
): Promise<QueryResult> {
  return api.post<QueryResult>("/v1/query/aggregate", req, { scope });
}

export function runSearch(req: QueryRequest, scope: string): Promise<QueryResult> {
  return api.post<QueryResult>("/v1/query/search", req, { scope });
}

export function runTime(req: QueryRequest, scope: string): Promise<QueryResult> {
  return api.post<QueryResult>("/v1/query/time", req, { scope });
}

export function runEntity(req: QueryRequest, scope: string): Promise<QueryResult> {
  return api.post<QueryResult>("/v1/query/entity", req, { scope });
}

export function runRelation(req: QueryRequest, scope: string): Promise<QueryResult> {
  return api.post<QueryResult>("/v1/query/relation", req, { scope });
}
