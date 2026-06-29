// Query API（typed 封装）。
// 后端契约（均 scope-aware，经 bridge `_scope` 路由 → QueryService(_ctx(request))）：
//   POST /v1/query/aggregate  {query, top_k, time_from, time_to, person, source, mix, mix_top_k} → v1_router.py:431
//   POST /v1/query/search      {query, top_k}                                                  → v1_router.py:366
//   POST /v1/query/time        {query, top_k, time_from, time_to, person, source}             → v1_router.py:376
//   POST /v1/query/entity      {entity_name}                                                   → v1_router.py:393
//   POST /v1/query/relation    {subject, predicate, object}                                   → v1_router.py:403
//   POST /v1/query/episode     （复用 episodeApi.queryEpisodes，不在本文件重复）
//
// 返回结构宽松（各模式字段不一），统一以 QueryResult 承接，由 utils/queryText 摊平为展示项。

import { api } from "./api";

/** 查询模式（与 legacy query-mode 按钮 data-mode 一一对应）。 */
export type QueryMode =
  | "aggregate"
  | "search"
  | "time"
  | "episode"
  | "relation"
  | "entity";

/** 查询请求参数（relation 模式仅用 subject/predicate/object，其余用 query 系列）。 */
export interface QueryRequest {
  query?: string;
  top_k?: number;
  time_from?: string | null;
  time_to?: string | null;
  person?: string | null;
  source?: string | null;
  mix?: boolean;
  mix_top_k?: number;
  entity_name?: string;
  subject?: string;
  predicate?: string;
  object?: string;
  include_paragraphs?: boolean;
}

/** 查询返回项（字段宽松，各模式不一）。 */
export interface QueryResultItem {
  [k: string]: unknown;
}

/** 查询返回 envelope：count + 任一结果数组字段。 */
export interface QueryResult {
  query_type?: string;
  query?: string;
  count?: number;
  mixed_results?: QueryResultItem[];
  results?: QueryResultItem[];
  relations?: QueryResultItem[];
  paragraphs?: QueryResultItem[];
  neighbors?: string[];
  [k: string]: unknown;
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
