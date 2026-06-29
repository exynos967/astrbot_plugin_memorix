// Memory API（typed 封装）。
// 后端契约：
//   POST /v1/memory/status    → v1_router.py:549（MemoryService.status，scope-aware）
//   POST /v1/memory/reinforce → v1_router.py:562（body: {id}）
//   POST /v1/memory/protect   → v1_router.py:556（body: {id, hours}）
//   POST /v1/memory/freeze    → v1_router.py:568（body: {id}）
//   GET  /api/memory/recycle_bin → routes_compat.py:928（?limit=）
//   POST /api/memory/restore    → routes_compat.py:951（body: {hash, type}）
//
// 与 legacy 一致：status/actions 走 /v1（scope-aware），recycle/restore 走 /api。
// 统一经 effectiveScope 传 scope（修 C5 类作用域不一致问题）。

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

/** /v1/memory/{reinforce,protect,freeze} 返回结构（字段宽松）。 */
export interface MemoryActionResult {
  success?: boolean;
  message?: string;
  count?: number;
  revived?: number;
  frozen_edges?: number;
  mode?: string;
  [key: string]: unknown;
}

/** 回收站单项（关系或实体）。 */
export interface RecycleItem {
  hash?: string;
  type?: string;
  subject?: string;
  object?: string;
  predicate?: string;
  name?: string;
  content?: string;
  deleted_at?: number;
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
): Promise<MemoryActionResult> {
  const body = action === "protect" ? { id, hours: 24 } : { id };
  return api.post<MemoryActionResult>(ACTION_URL[action], body, { scope });
}

export function fetchRecycleBin(limit: number, scope: string): Promise<{ items: RecycleItem[] }> {
  return api.get<{ items: RecycleItem[] }>(`/api/memory/recycle_bin?limit=${limit}`, { scope });
}

export function restoreMemory(hash: string, type: string, scope: string): Promise<MemoryActionResult> {
  return api.post<MemoryActionResult>("/api/memory/restore", { hash, type: type || "relation" }, { scope });
}
