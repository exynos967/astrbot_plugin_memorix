// Source API（typed 封装）。
// 后端契约：
//   POST /api/source/list         {node_id?, edge_source?, edge_target?} → routes_compat.py:746
//        ─ 全空返回 {mode:"summary", sources:[{source,count,last_updated}]}
//        ─ node_id 或 edge_source+edge_target 非空返回 {sources:[{hash,content,created_at,source}]}
//   POST /api/source/batch_delete {source} → routes_compat.py:800（{success,count,message}）
//   POST /v1/delete/paragraph     {paragraph_hash} → v1_router.py:513（scope-aware，经 bridge _scope 路由，须传 scope）
//   注：/api/source/* 挂在 routes_compat 全局 app（NOT scope-aware）；/v1/delete/paragraph 走 v1_router（scope-aware）。

import { api } from "./api";

export interface SourceSummaryItem {
  source?: string;
  count?: number;
  last_updated?: number;
}

export interface SourceParagraphItem {
  hash?: string;
  content?: string;
  created_at?: number;
  source?: string;
}

export type SourceListItem = SourceSummaryItem | SourceParagraphItem;

export interface SourceListResult {
  mode?: string;
  sources?: SourceListItem[];
}

export interface SourceDeleteResult {
  success?: boolean;
  count?: number;
  message?: string;
}

export interface ParagraphDeleteResult {
  success?: boolean;
  paragraph_hash?: string;
  relation_prune_count?: number;
  deleted_vectors?: number;
}

export function fetchSourceList(req: {
  node_id?: string | null;
  edge_source?: string | null;
  edge_target?: string | null;
}): Promise<SourceListResult> {
  const body: Record<string, string> = {};
  if (req.node_id) body.node_id = req.node_id;
  if (req.edge_source) body.edge_source = req.edge_source;
  if (req.edge_target) body.edge_target = req.edge_target;
  return api.post<SourceListResult>("/api/source/list", body);
}

export function batchDeleteSource(source: string): Promise<SourceDeleteResult> {
  return api.post<SourceDeleteResult>("/api/source/batch_delete", { source });
}

export function deleteParagraph(hash: string, scope: string): Promise<ParagraphDeleteResult> {
  return api.post<ParagraphDeleteResult>(
    "/v1/delete/paragraph",
    { paragraph_hash: hash },
    { scope },
  );
}
