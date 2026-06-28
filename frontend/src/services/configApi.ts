// Dashboard / 配置相关 API（typed 封装）。
// 后端契约参照：
//   /v1/query/stats      → memorix/amemorix/routers/v1_router.py:450（scope 经 bridge _scope 路由）
//   /v1/dashboard/status → v1_router.py:310
//   /v1/runtime/self_check → v1_router.py:508（POST，force 走 query 参数）
//   /api/config          → memorix/webui/routes_compat.py:1332
//   /api/scopes          → memorix/webui/plugin_page_bridge.py:324（bridge 层，返回已知 scope 列表）
//
// C5 关键：_scope 是 bridge 层查询参数，用于路由到对应 scope 的嵌入式 app（plugin_page_bridge.py:361-371）。
// legacy loadStats 不带 _scope → 命中 scope_resolver() 默认 scope；loadGraph 带下拉 scope → 两者源不一致。
// 新实现统一传 effectiveScope（currentScope || resolvedScope），见 useGraphStore。

import { api } from "./api";

/** /api/scopes 返回的单个 scope 项。 */
export interface ScopeOption {
  value: string;
  label: string;
}

/** /api/scopes 返回结构（bridge 层，与 _scope 无关）。 */
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

/** /v1/runtime/self_check 返回结构（字段宽松，运行时报告附加键不定）。 */
export interface RuntimeReport {
  ok: boolean;
  message?: string;
  code?: string;
  embedding?: { dimension?: number; expected_dimension?: number; model?: string };
  dimension?: number;
  expected_dimension?: number;
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

/** /api/config 返回结构（GET，脱敏只读；不含 config_persistence）。 */
export interface ConfigPayload {
  auto_save_enabled: boolean;
  auto_save_interval: number;
  config?: Record<string, unknown>;
}

/** 已知 scope 列表（bridge 层，与 _scope 无关）。 */
export function fetchScopes(): Promise<ScopesPayload> {
  return api.get<ScopesPayload>("/api/scopes");
}

/** 图谱/向量统计。统一传 scope 修 C5。 */
export function fetchStats(scope: string): Promise<QueryStats> {
  return api.get<QueryStats>("/v1/query/stats", { scope });
}

/** Dashboard 服务分区 + 调用趋势。统一传 scope。 */
export function fetchDashboardStatus(scope: string): Promise<DashboardStatus> {
  return api.get<DashboardStatus>("/v1/dashboard/status", { scope });
}

/** 运行时自检（embedding 维度等）。force 走 query 参数，与 legacy 一致。 */
export function fetchRuntimeSelfCheck(force: boolean): Promise<RuntimeReport> {
  return api.post<RuntimeReport>(
    `/v1/runtime/self_check?force=${force ? "true" : "false"}`,
    {},
  );
}

/** 脱敏只读配置（用于 dashboard autosave 指示；表单渲染在 P3）。 */
export function fetchConfig(): Promise<ConfigPayload> {
  return api.get<ConfigPayload>("/api/config");
}
