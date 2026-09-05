import { api } from "./api";

interface AdminResult { success?: boolean; error?: string }

export async function memoryAdmin<T extends AdminResult>(
  endpoint: "facts" | "person/aliases" | "memory/delete-admin",
  action: string,
  payload: Record<string, unknown>,
  scope: string,
): Promise<T> {
  const result = await api.post<T>(`/v1/${endpoint}`, { action, payload }, { scope });
  if (result.success === false) throw new Error(result.error || "操作失败");
  return result;
}

export interface FactClaim {
  claim_id: string;
  fact_key: string;
  value_text: string;
  status: string;
  stability: string;
  cardinality: string;
}

export interface FactList extends AdminResult { items: FactClaim[] }
export interface AliasDetails extends AdminResult {
  manual_aliases: string[] | null;
  derived_aliases: string[];
  effective_aliases: string[];
}
export interface DeleteOperation {
  operation_id: string;
  mode: string;
  status: string;
  created_at: number;
  summary?: { counts?: Record<string, number> };
}
export interface DeleteOperations extends AdminResult { items: DeleteOperation[] }
