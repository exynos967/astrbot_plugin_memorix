// Source API（typed 封装）。
// 后端契约（均 NOT scope-aware，直接操作插件级全局 store，故不传 scope）：
//   POST /api/source/list         {node_id?, edge_source?, edge_target?} → routes_compat.py:746
//        ─ 全空返回 {mode:"summary", sources:[{source,count,last_updated}]}
//        ─ node_id 或 edge_source+edge_target 非空返回 {sources:[{hash,content,created_at,source}]}
//   POST /api/source/batch_delete {source} → routes_compat.py:800（{success,count,message}）
//   POST /v1/delete/paragraph     {paragraph_hash} → v1_router.py:513（{success,relation_prune_count,deleted_vectors}）

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

export function deleteParagraph(hash: string): Promise<ParagraphDeleteResult> {
  return api.post<ParagraphDeleteResult>("/v1/delete/paragraph", { paragraph_hash: hash });
}
