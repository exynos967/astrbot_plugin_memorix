// Memory API（typed 封装，统一经 effectiveScope 路由作用域）。
// 后端契约：
//   POST /v1/memory/status       → MemoryService.status
//   POST /v1/memory/reinforce    → {id}
//   POST /v1/memory/protect      → {id, hours}（hours <= 0 为永久置顶 pin，hours > 0 为限时 TTL）
//   POST /v1/memory/freeze       → {id}
//   GET  /api/memory/recycle_bin → ?limit=&_scope=（返回 {items, error?}）
//   POST /v1/memory/restore      → {hash, type: "relation" | "entity" | "paragraph"}

import { api } from "./api";

/** /v1/memory/status 返回结构。 */
export interface MemoryStatus {
  config: Record<string, unknown>;
  active_relations: number;
  inactive_relations: number;
  recycle_bin_relations: number;
  pinned_relations: number;
  ttl_protected_relations: number;
}

/** /v1/memory/{reinforce,protect,freeze,restore} 返回结构。 */
export interface MemoryActionResult {
  success?: boolean;
  message?: string;
  count?: number;
  revived?: number;
  frozen_edges?: number;
  mode?: string;
  protected_until?: number | null;
  restored?: number;
  already_live?: boolean;
  [key: string]: unknown;
}

/** 回收站单项（关系、实体或段落）。 */
export interface RecycleItem {
  hash?: string;
  type?: "relation" | "entity" | "paragraph" | string;
  subject?: string;
  object?: string;
  predicate?: string;
  name?: string;
  content?: string;
  source?: string;
  deleted_at?: number;
}

export interface RecycleBinResponse {
  items: RecycleItem[];
  error?: string;
}

export type MemoryAction = "reinforce" | "protect" | "freeze";

const ACTION_URL: Record<MemoryAction, string> = {
  reinforce: "/v1/memory/reinforce",
  protect: "/v1/memory/protect",
  freeze: "/v1/memory/freeze",
};

export function fetchMemoryStatus(scope: string): Promise<MemoryStatus> {
  return api.post<MemoryStatus>("/v1/memory/status", {}, { scope });
}

export function runMemoryAction(
  action: MemoryAction,
  id: string,
  scope: string,
  hours = 24,
): Promise<MemoryActionResult> {
  const body = action === "protect" ? { id, hours } : { id };
  return api.post<MemoryActionResult>(ACTION_URL[action], body, { scope });
}

export function fetchRecycleBin(
  limit: number,
  scope: string,
): Promise<RecycleBinResponse> {
  return api.get<RecycleBinResponse>(`/api/memory/recycle_bin?limit=${limit}`, { scope });
}

export function restoreMemory(
  hash: string,
  type: string,
  scope: string,
): Promise<MemoryActionResult> {
  return api.post<MemoryActionResult>(
    "/v1/memory/restore",
    { hash, type: type || "relation" },
    { scope },
  );
}
