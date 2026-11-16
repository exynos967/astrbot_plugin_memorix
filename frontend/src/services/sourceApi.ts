// Source API（typed 封装，全量透传 scope 保障跨作用域隔离）。
// 后端契约：
//   POST /api/source/list         {node_id?, edge_source?, edge_target?, source?} + ?_scope=
//        ─ 全空返回 {mode:"summary", sources:[{source,count,last_updated}]}
//        ─ node_id / source / edge_source+edge_target 非空返回 {sources:[{hash,content,created_at,source}]}
//   POST /api/source/batch_delete {source} + ?_scope=
//   POST /v1/delete/paragraph     {paragraph_hash} + ?_scope=

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

export interface SourceListQuery {
  node_id?: string | null;
  edge_source?: string | null;
  edge_target?: string | null;
  source?: string | null;
}

export interface SourceListResult {
  mode?: string;
  sources?: SourceListItem[];
}

export interface DeletedCounts {
  paragraph?: number;
  entity?: number;
  relation?: number;
}

export interface SourceDeleteResult {
  success?: boolean;
  count?: number;
  message?: string;
  operation_id?: string;
  deleted?: DeletedCounts;
}

export interface ParagraphDeleteResult {
  success?: boolean;
  paragraph_hash?: string;
  relation_prune_count?: number;
  deleted_vectors?: number;
  operation_id?: string;
  deleted?: DeletedCounts;
}

export function fetchSourceList(
  req: SourceListQuery,
  scope?: string,
): Promise<SourceListResult> {
  const body: Record<string, string> = {};
  if (req.node_id) body.node_id = req.node_id;
  if (req.edge_source) body.edge_source = req.edge_source;
  if (req.edge_target) body.edge_target = req.edge_target;
  if (req.source) body.source = req.source;
  return api.post<SourceListResult>("/api/source/list", body, { scope });
}

export function batchDeleteSource(
  source: string,
  scope?: string,
): Promise<SourceDeleteResult> {
  return api.post<SourceDeleteResult>("/api/source/batch_delete", { source }, { scope });
}

export function deleteParagraph(
  hash: string,
  scope: string,
): Promise<ParagraphDeleteResult> {
  return api.post<ParagraphDeleteResult>(
    "/v1/delete/paragraph",
    { paragraph_hash: hash },
    { scope },
  );
}
