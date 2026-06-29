// 查询结果展示工具：把各模式返回的 envelope 摊平为统一的展示项。
// 对应 legacy renderGenericResults（index.html 行 4258-4286），改写为纯函数 + Vue v-for。
//
// envelope 可能字段：mixed_results / results / relations / paragraphs / neighbors。
// 每项标题取 title | display_name | person_name | entity_name | subject | hash | episode_id | type；
// 若同时有 subject+object 则拼成 "subject → object"。
// 正文取 summary | content | text | object | message | predicate。

import type { QueryResult, QueryResultItem } from "@/services/queryApi";

/** 统一展示项：标题、正文、标签、原始数据。 */
export interface QueryDisplayItem {
  title: string;
  body: string;
  tags: string[];
  raw: QueryResultItem;
}

function str(v: unknown): string {
  return v == null ? "" : String(v);
}

function pickTitle(item: QueryResultItem): string {
  const title =
    item.title ||
    item.display_name ||
    item.person_name ||
    item.entity_name ||
    item.subject ||
    item.hash ||
    item.episode_id ||
    item.type ||
    "";
  if (item.subject && item.object) return `${str(item.subject)} → ${str(item.object)}`;
  return str(title) || "Result";
}

function pickBody(item: QueryResultItem): string {
  const body =
    item.summary || item.content || item.text || item.object || item.message || item.predicate;
  return str(body);
}

function pickTags(item: QueryResultItem, payload: QueryResult): string[] {
  const tags: string[] = [];
  const t = item.type || item.result_type || payload.query_type;
  if (t) tags.push(str(t));
  if (item.hash) tags.push(`hash ${str(item.hash).slice(0, 8)}`);
  if (item.score != null) {
  const score = Number(item.score);
    if (!Number.isNaN(score)) tags.push(`score ${score.toFixed(3)}`);
  }
  if (item.source) tags.push(`source ${str(item.source)}`);
  return tags.filter(Boolean);
}

/** 从 envelope 摊平为展示项列表（与 legacy renderGenericResults 取值一致）。 */
export function buildQueryItems(payload: QueryResult): QueryDisplayItem[] {
  let items: QueryResultItem[] = [];
  if (Array.isArray(payload?.mixed_results)) items = payload.mixed_results;
  else if (Array.isArray(payload?.results)) items = payload.results;
  else if (Array.isArray(payload?.relations)) items = payload.relations;
  else if (Array.isArray(payload?.paragraphs)) items = payload.paragraphs;
  else if (Array.isArray(payload?.neighbors)) {
    items = (payload.neighbors as string[]).map((x) => ({ type: "neighbor", title: x }));
  }
  return items.map((item) => ({
    title: pickTitle(item),
    body: pickBody(item),
    tags: pickTags(item, payload),
    raw: item,
  }));
}

/** meta 文案："{mode} · {n} 条结果"，与 legacy runQuery 一致。 */
export function queryMetaLabel(mode: string, payload: QueryResult): string {
  const n = payload.count ?? payload.results?.length ?? payload.relations?.length ?? "-";
  return `${mode} · ${n} 条结果`;
}

/** 截断正文用于列表预览（与 legacy .slice(0, 900) 一致）。 */
export function truncateBody(body: string, max = 900): string {
  return body.length > max ? body.slice(0, max) : body;
}
