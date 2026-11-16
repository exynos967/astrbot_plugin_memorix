// 人物画像、登记目录、别名覆盖、结构化事实账本与画像证据链接口封装（随当前记忆作用域路由）。

import { api } from "./api";

/** /v1/person/registry/list 单项。 */
export interface PersonRegistryItem {
  person_id: string;
  display_name?: string;
  person_name?: string;
  nickname?: string;
  user_id?: string;
  platform?: string;
  group_nick_name?: unknown;
  aliases?: string[];
  last_know?: number;
  has_snapshot?: boolean;
  has_override?: boolean;
  latest_profile_updated_at?: number;
}

export interface PersonRegistryList {
  success?: boolean;
  items: PersonRegistryItem[];
  total?: number;
  page?: number;
  page_size?: number;
}

export interface PersonRegistryUpsertRequest {
  person_id: string;
  person_name?: string;
  nickname?: string;
  user_id?: string;
  platform?: string;
  group_nick_name?: string[] | string | null;
  memory_points?: unknown;
  last_know?: number | null;
  metadata?: Record<string, unknown>;
}

export interface PersonRegistryUpsertResult {
  success?: boolean;
  item?: PersonRegistryItem;
  error?: string;
}

export interface PersonQueryRequest {
  person_id?: string;
  person_keyword?: string;
  top_k?: number;
  force_refresh?: boolean;
}

export interface PersonRelationEdge {
  hash?: string;
  subject?: string;
  predicate?: string;
  object?: string;
  confidence?: number;
  [k: string]: unknown;
}

export interface PersonEvidence {
  hash?: string;
  type?: string;
  score?: number;
  content?: string;
  source?: string;
  [k: string]: unknown;
}

/** /v1/person/query 返回的画像对象（兼容命中与未命中返回结构）。 */
export interface PersonProfile {
  success?: boolean;
  error?: string;
  person_id?: string;
  person_name?: string;
  profile_text?: string;
  auto_profile_text?: string;
  aliases?: string[];
  relation_edges?: PersonRelationEdge[];
  vector_evidence?: PersonEvidence[];
  evidence_ids?: string[];
  has_manual_override?: boolean;
  manual_override_text?: string;
  override_text?: string;
  override_updated_at?: number | null;
  override_updated_by?: string;
  profile_source?: string;
  from_cache?: boolean;
  snapshot_id?: string | number;
  profile_version?: number;
  updated_at?: number;
  expires_at?: number | null;
  uncertain_fact_count?: number;
  [k: string]: unknown;
}

export interface PersonOverrideResult {
  success?: boolean;
  deleted?: boolean;
  person_id?: string;
  override?: {
    person_id?: string;
    override_text?: string;
    updated_at?: number;
    updated_by?: string;
    source?: string;
  };
  profile?: PersonProfile;
  error?: string;
  [k: string]: unknown;
}

/** v2.0 结构化事实账本 FactClaim。 */
export interface PersonFactClaim {
  claim_id: string;
  scope_type?: string;
  scope_id?: string;
  fact_key: string;
  value_text: string;
  value_normalized?: string;
  polarity: "positive" | "negative" | string;
  cardinality: "single" | "set" | string;
  stability: "stable" | "temporal" | "uncertain" | string;
  profile_section:
    | "identity_settings"
    | "relationship_settings"
    | "stable_facts"
    | "interaction_preferences"
    | "recent_interactions"
    | "uncertain_notes"
    | string;
  authority?: string;
  status: "active" | "conflicted" | "superseded" | "retracted" | string;
  confidence?: number;
  valid_from?: number | null;
  valid_to?: number | null;
  updated_at?: number | null;
  created_at?: number | null;
}

export interface PersonAliasDetails {
  success?: boolean;
  error?: string;
  manual_aliases: string[] | null;
  derived_aliases: string[];
  effective_aliases?: string[];
  refresh_queued?: boolean;
}

export interface ProfileAdminEvidenceItem {
  evidence_key: string;
  evidence_type: "relation" | "paragraph" | "person_fact" | "chat_summary" | string;
  hash: string;
  content: string;
  source?: string;
  source_type?: string;
  score?: number | null;
  confidence?: number | null;
  deletable?: boolean;
  not_deletable_reason?: string;
}

export interface ProfileAdminEvidenceResponse {
  success?: boolean;
  error?: string;
  person_id?: string;
  person_name?: string;
  evidence: ProfileAdminEvidenceItem[];
  evidence_count?: number;
  uncertain_fact_count?: number;
  raw_profile?: PersonProfile;
}

export interface ProfileCorrectEvidenceResponse {
  success?: boolean;
  error?: string;
  person_id?: string;
  operation_id?: string;
  refreshed_profile?: PersonProfile;
  refreshed_evidence?: ProfileAdminEvidenceResponse;
}

export function fetchPersonRegistry(
  keyword: string,
  page = 1,
  pageSize = 30,
  scope?: string,
): Promise<PersonRegistryList> {
  const kw = encodeURIComponent(keyword || "");
  return api.get<PersonRegistryList>(
    `/v1/person/registry/list?keyword=${kw}&page=${page}&page_size=${pageSize}`,
    { scope },
  );
}

export async function upsertPersonRegistry(
  payload: PersonRegistryUpsertRequest,
  scope?: string,
): Promise<PersonRegistryUpsertResult> {
  const res = await api.post<PersonRegistryUpsertResult>("/v1/person/registry/upsert", payload, { scope });
  if (res && res.success === false) {
    throw new Error(res.error || "登记人物失败");
  }
  return res;
}

export function queryPerson(req: PersonQueryRequest, scope?: string): Promise<PersonProfile> {
  return api.post<PersonProfile>("/v1/person/query", req, { scope });
}

export function savePersonOverride(
  personId: string,
  overrideText: string,
  updatedBy = "webui",
  scope?: string,
): Promise<PersonOverrideResult> {
  return api.post<PersonOverrideResult>(
    "/v1/person/override",
    {
      person_id: personId,
      override_text: overrideText,
      updated_by: updatedBy,
    },
    { scope },
  );
}

export function clearPersonOverride(personId: string, scope?: string): Promise<PersonOverrideResult> {
  return api.delete<PersonOverrideResult>("/v1/person/override", { person_id: personId }, { scope });
}

export async function callPersonAliasesAdmin<T extends { success?: boolean; error?: string }>(
  action: "get_aliases" | "set_aliases" | "delete_aliases",
  payload: Record<string, unknown>,
  scope?: string,
): Promise<T> {
  const res = await api.post<T>("/v1/person/aliases", { action, payload }, { scope });
  if (res && res.success === false) {
    throw new Error(res.error || "人物别名操作失败");
  }
  return res;
}

export async function callPersonFactsAdmin<T extends { success?: boolean; error?: string }>(
  action: "get" | "list" | "create" | "update" | "retract" | "restore",
  payload: Record<string, unknown>,
  scope?: string,
): Promise<T> {
  const res = await api.post<T>("/v1/facts", { action, payload }, { scope });
  if (res && res.success === false) {
    throw new Error(res.error || "人物事实操作失败");
  }
  return res;
}

/** 兜底从 raw_profile 组装结构化证据链（兼容精简版 admin_service 返回空 evidence 列表的场景）。 */
function normalizeFallbackEvidence(raw?: PersonProfile): ProfileAdminEvidenceItem[] {
  if (!raw) return [];
  const items: ProfileAdminEvidenceItem[] = [];
  const seen = new Set<string>();

  for (const [idx, edge] of (raw.relation_edges || []).entries()) {
    const hash = String(edge.hash || "").trim();
    const key = `relation:${hash || idx}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const content = [edge.subject, edge.predicate, edge.object]
      .map((s) => String(s || "").trim())
      .filter(Boolean)
      .join(" — ");
    items.push({
      evidence_key: key,
      evidence_type: "relation",
      hash,
      content: content || "-",
      source: String(edge.source_paragraph || ""),
      confidence: typeof edge.confidence === "number" ? edge.confidence : null,
      score: null,
      deletable: Boolean(hash),
      not_deletable_reason: hash ? "" : "缺少关系 hash",
    });
  }

  for (const [idx, ev] of (raw.vector_evidence || []).entries()) {
    const hash = String(ev.hash || "").trim();
    const evType = String(ev.type || "paragraph").trim() || "paragraph";
    const key = `${evType}:${hash || idx}`;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({
      evidence_key: key,
      evidence_type: evType,
      hash,
      content: String(ev.content || "").trim() || "-",
      source: String(ev.source || ""),
      score: typeof ev.score === "number" ? ev.score : null,
      confidence: null,
      deletable: Boolean(hash),
      not_deletable_reason: hash ? "" : "缺少段落 hash",
    });
  }

  return items;
}

export async function fetchProfileEvidence(
  params: { person_id?: string; person_keyword?: string; limit?: number; force_refresh?: boolean },
  scope?: string,
): Promise<ProfileAdminEvidenceResponse> {
  const res = await api.post<ProfileAdminEvidenceResponse>(
    "/v1/admin/memory_profile_admin",
    {
      action: "evidence",
      payload: {
        person_id: params.person_id || "",
        person_keyword: params.person_keyword || "",
        limit: params.limit ?? 20,
        force_refresh: Boolean(params.force_refresh),
      },
    },
    { scope },
  );
  if (res && res.success === false) {
    throw new Error(res.error || "获取画像证据链失败");
  }
  const normalized =
    Array.isArray(res.evidence) && res.evidence.length > 0
      ? res.evidence
      : normalizeFallbackEvidence(res.raw_profile);
  return {
    ...res,
    evidence: normalized,
    evidence_count: normalized.length,
  };
}

export async function correctProfileEvidence(
  params: {
    person_id: string;
    person_keyword?: string;
    evidence_type: string;
    hash: string;
    reason?: string;
  },
  scope?: string,
): Promise<ProfileCorrectEvidenceResponse> {
  const res = await api.post<ProfileCorrectEvidenceResponse>(
    "/v1/admin/memory_profile_admin",
    {
      action: "correct_evidence",
      payload: {
        person_id: params.person_id,
        person_keyword: params.person_keyword || "",
        evidence_type: params.evidence_type,
        hash: params.hash,
        requested_by: "webui",
        reason: params.reason || "webui_profile_evidence_correction",
        refresh: true,
      },
    },
    { scope },
  );
  if (res && res.success === false) {
    throw new Error(res.error || "剔除画像证据失败");
  }
  return res;
}
