// Person API（typed 封装）。
// 后端契约（均 NOT scope-aware，直接操作插件级全局 store，故不传 scope）：
//   GET     /v1/person/registry/list ?keyword=&page=1&page_size=30 → v1_router.py:627
//   POST    /v1/person/query       {person_keyword, top_k, force_refresh} → v1_router.py:580
//   POST    /v1/person/override    {person_id, override_text, updated_by} → v1_router.py:595
//   DELETE  /v1/person/override     {person_id} → v1_router.py:608
//
// 与 legacy 一致：registry/query/override 全走 /v1。person_profile.* 与之等价，沿用 /v1。

import { api } from "./api";

/** /v1/person/registry/list 单项。 */
export interface PersonRegistryItem {
  person_id: string;
  display_name?: string;
  person_name?: string;
  nickname?: string;
  user_id?: string;
  platform?: string;
  aliases?: string[];
  last_know?: number;
  has_snapshot?: boolean;
  has_override?: boolean;
  latest_profile_updated_at?: number;
}

export interface PersonRegistryList {
  items: PersonRegistryItem[];
  total?: number;
  page?: number;
  page_size?: number;
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
  [k: string]: unknown;
}

/** /v1/person/query 返回的画像对象（字段宽松）。 */
export interface PersonProfile {
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
  snapshot_id?: string;
  profile_version?: number;
  updated_at?: number;
  expires_at?: number | null;
  [k: string]: unknown;
}

export interface PersonOverrideResult {
  success?: boolean;
  person_id?: string;
  override?: {
    person_id?: string;
    override_text?: string;
    updated_at?: number;
    updated_by?: string;
    source?: string;
  };
  profile?: PersonProfile;
  [k: string]: unknown;
}

export function fetchPersonRegistry(
  keyword: string,
  page = 1,
  pageSize = 30,
): Promise<PersonRegistryList> {
  const kw = encodeURIComponent(keyword || "");
  return api.get<PersonRegistryList>(
    `/v1/person/registry/list?keyword=${kw}&page=${page}&page_size=${pageSize}`,
  );
}

export function queryPerson(req: PersonQueryRequest): Promise<PersonProfile> {
  return api.post<PersonProfile>("/v1/person/query", req);
}

export function savePersonOverride(
  personId: string,
  overrideText: string,
  updatedBy = "webui",
): Promise<PersonOverrideResult> {
  return api.post<PersonOverrideResult>("/v1/person/override", {
    person_id: personId,
    override_text: overrideText,
    updated_by: updatedBy,
  });
}

export function clearPersonOverride(personId: string): Promise<PersonOverrideResult> {
  return api.delete<PersonOverrideResult>("/v1/person/override", { person_id: personId });
}
