// Graph API（typed 封装）。
// 后端契约（routes_compat.py，部分 scope-aware 经 bridge _scope）：
//   GET  /api/graph?exclude_leaf=&source=&density=&_scope= → routes_compat.py:168
//        ─ 返回 {nodes:[{id,label,...}], edges:[{id,from,to,value,label,arrows}]}
//   GET  /api/scopes → plugin_page_bridge.py:324（bridge 层，返回 {current, scopes[{value,label}]}，与 _scope 无关）
//   POST /api/node         {node_id, label?}                       → :645 {success, added_count, node_id}
//   POST /api/edge         {source, target, weight=1, predicate?} → :666 {success, added_count, predicate, relation_hash}
//   PUT  /api/node/rename  {old_id, new_id}                        → :702 {success, old_id, new_id}
//   DELETE /api/node       {node_id}                               → :599 {success, deleted_count}
//   DELETE /api/edge       {source, target}                        → :621 {success, deleted_relations}
//   POST /api/edge/weight  {source, target, weight}                → :576 {success, new_weight}
//
// C5：/api/graph 经 bridge `_scope` 路由，scope 由调用方传 effectiveScope。

import { api } from "./api";

/** 图谱节点（后端原始字段宽松）。 */
export interface GraphNode {
  id: string;
  label?: string;
  is_deleted?: boolean;
  is_ghost?: boolean;
  [k: string]: unknown;
}

/** 图谱边。 */
export interface GraphEdge {
  id?: string;
  from: string;
  to: string;
  value?: number;
  label?: string;
  arrows?: string | boolean;
  dashes?: boolean;
  is_active?: boolean;
  is_pinned?: boolean;
  is_protected?: boolean;
  protected_until?: number | null;
  predicates?: string[];
  color?: unknown;
  [k: string]: unknown;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  debug?: Record<string, unknown>;
}

export interface GraphQuery {
  excludeLeaf?: boolean;
  density?: number;
  source?: string;
  scope?: string;
}

export function fetchGraph(req: GraphQuery): Promise<GraphData> {
  const params = new URLSearchParams();
  if (req.excludeLeaf) params.set("exclude_leaf", "true");
  if (req.density != null) params.set("density", String(req.density));
  if (req.source) params.set("source", req.source);
  const qs = params.toString();
  const url = qs ? `/api/graph?${qs}` : "/api/graph";
  return api.get<GraphData>(url, { scope: req.scope });
}

export function createNode(nodeId: string, label?: string, scope?: string): Promise<NodeOpResult> {
  return api.post<NodeOpResult>("/api/node", { node_id: nodeId, label: label || null }, { scope });
}

export function deleteNode(nodeId: string, scope?: string): Promise<NodeOpResult> {
  return api.delete<NodeOpResult>("/api/node", { node_id: nodeId }, { scope });
}

export function renameNode(oldId: string, newId: string, scope?: string): Promise<RenameResult> {
  return api.put<RenameResult>("/api/node/rename", { old_id: oldId, new_id: newId }, { scope });
}

export function createEdge(
  source: string,
  target: string,
  weight = 1,
  predicate?: string,
  scope?: string,
): Promise<EdgeOpResult> {
  return api.post<EdgeOpResult>(
    "/api/edge",
    { source, target, weight, predicate: predicate || null },
    { scope },
  );
}

export function deleteEdge(source: string, target: string, scope?: string): Promise<EdgeDeleteResult> {
  return api.delete<EdgeDeleteResult>("/api/edge", { source, target }, { scope });
}

export function updateEdgeWeight(
  source: string,
  target: string,
  weight: number,
  scope?: string,
): Promise<WeightResult> {
  return api.post<WeightResult>("/api/edge/weight", { source, target, weight }, { scope });
}

export interface NodeOpResult {
  success?: boolean;
  added_count?: number;
  deleted_count?: number;
  node_id?: string;
}

export interface RenameResult {
  success?: boolean;
  old_id?: string;
  new_id?: string;
}

export interface EdgeOpResult {
  success?: boolean;
  added_count?: number;
  predicate?: string;
  relation_hash?: string;
}

export interface EdgeDeleteResult {
  success?: boolean;
  deleted_relations?: number;
}

export interface WeightResult {
  success?: boolean;
  new_weight?: number;
}
