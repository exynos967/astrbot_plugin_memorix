// 查询结果展示工具：把各模式返回的 envelope 格式化为中文展示项与诊断徽标。
// 修复点：
// - 关系三元组完整展示 "subject —[predicate]→ object"，不再吞掉 predicate 或把 object 误当正文重复显示
// - 聚合模式支持 3 分支诊断（search / time / episode）、errors 提取、source_branches 与 fusion_score 中文标签
// - 实体模式支持同时拆出「关联关系 / 提及段落 / 邻接实体」三大区块，避免 else-if 吞掉后两项
// - 全中文标签（消除 Result / score / source 等英文裸标签）

import type { QueryMode, QueryResult, QueryResultItem } from "@/services/queryApi";
import { formatTs } from "./time";

export const QUERY_MODE_LABELS: Record<QueryMode, string> = {
  auto: "智能路由",
  aggregate: "多路聚合",
  search: "语义检索",
  time: "时序检索",
  episode: "情景检索",
  relation: "关系三元组",
  entity: "实体详情",
};

const RESULT_TYPE_LABELS: Record<string, string> = {
  paragraph: "记忆段落",
  relation: "图谱关系",
  episode: "情景记忆",
  entity: "图谱实体",
  neighbor: "邻接实体",
  search: "语义召回",
  time: "时序召回",
  hybrid: "混合召回",
  aggregate: "聚合结果",
};

const BRANCH_LABELS: Record<string, string> = {
  search: "语义",
  time: "时序",
  episode: "情景",
};

export interface QueryTagBadge {
  text: string;
  tone?: "" | "ok" | "warn" | "bad";
  mono?: boolean;
}

export interface QueryDisplayItem {
  title: string;
  body: string;
  tags: string[];
  badges: QueryTagBadge[];
  raw: QueryResultItem;
}

export interface AggregateBranchChip {
  key: string;
  label: string;
  detail: string;
  tone: "" | "ok" | "warn" | "bad";
}

export interface EntityDetailSections {
  entityName: string;
  relations: QueryDisplayItem[];
  paragraphs: QueryDisplayItem[];
  neighbors: string[];
}

function str(v: unknown): string {
  return v == null ? "" : String(v).trim();
}

export function queryModeLabel(mode: string): string {
  return (QUERY_MODE_LABELS as Record<string, string>)[mode] || mode;
}

export function routeReasonLabel(reason: string | undefined): string {
  if (!reason) return "";
  const map: Record<string, string> = {
    time_intent: "自动识别时间意图",
    semantic: "语义直达检索",
    hybrid: "时间窗 + 语义混合检索",
    fallback: "回退语义检索",
  };
  return map[reason] || `路由原因：${reason}`;
}

function formatTypeLabel(rawType: unknown): string {
  const key = str(rawType).toLowerCase();
  if (!key) return "";
  return RESULT_TYPE_LABELS[key] || str(rawType);
}

function pickTitle(item: QueryResultItem): string {
  if (item.subject && item.object) {
    const pred = str(item.predicate || item.label);
    return pred
      ? `${str(item.subject)} —[${pred}]→ ${str(item.object)}`
      : `${str(item.subject)} → ${str(item.object)}`;
  }
  const title =
    item.title ||
    item.display_name ||
    item.person_name ||
    item.entity_name ||
    item.name ||
    item.subject ||
    (item.source ? `来源：${str(item.source)}` : "") ||
    (item.episode_id ? `情景 ${str(item.episode_id).slice(0, 12)}` : "") ||
    (item.hash ? `片段 ${str(item.hash).slice(0, 10)}` : "");
  return str(title) || "检索结果";
}

function pickBody(item: QueryResultItem): string {
  if (item.subject && item.object) {
    return str(item.summary || item.content || item.text || item.evidence || item.message || "");
  }
  return str(item.summary || item.content || item.text || item.message || item.predicate || "");
}

function pickBadges(item: QueryResultItem, payload?: QueryResult): QueryTagBadge[] {
  const badges: QueryTagBadge[] = [];
  const rawType =
    item.type ||
    item.result_type ||
    (item.subject && item.object ? "relation" : item.episode_id ? "episode" : payload?.query_type);
  const typeText = formatTypeLabel(rawType);
  if (typeText) badges.push({ text: typeText });

  if (Array.isArray(item.source_branches) && item.source_branches.length > 0) {
    const names = item.source_branches
      .map((b) => BRANCH_LABELS[str(b)] || str(b))
      .filter(Boolean);
    if (names.length) {
      badges.push({ text: `命中：${names.join(" + ")}`, tone: "ok" });
    }
  }

  if (item.fusion_score != null) {
    const fScore = Number(item.fusion_score);
    if (Number.isFinite(fScore)) {
      badges.push({ text: `融合分 ${fScore.toFixed(4)}`, tone: "ok" });
    }
  }

  if (item.score != null) {
    const score = Number(item.score);
    if (Number.isFinite(score)) {
      badges.push({ text: `相关度 ${score.toFixed(3)}` });
    }
  }

  if (item.confidence != null) {
    const conf = Number(item.confidence);
    if (Number.isFinite(conf)) {
      badges.push({ text: `置信度 ${conf.toFixed(2)}` });
    }
  }

  if (item.source) {
    badges.push({ text: `来源：${str(item.source)}` });
  }

  if (item.created_at != null && Number.isFinite(Number(item.created_at))) {
    badges.push({ text: formatTs(Number(item.created_at)) });
  } else if (item.event_time_start != null && Number.isFinite(Number(item.event_time_start))) {
    badges.push({ text: formatTs(Number(item.event_time_start)) });
  }

  if (item.is_pinned) badges.push({ text: "永久置顶", tone: "warn" });
  if (item.is_inactive) badges.push({ text: "冷冻", tone: "warn" });

  if (item.hash) {
    badges.push({ text: `哈希 ${str(item.hash).slice(0, 8)}`, mono: true });
  }

  return badges;
}

export function normalizeDisplayItem(
  item: QueryResultItem,
  payload?: QueryResult,
): QueryDisplayItem {
  const badges = pickBadges(item, payload);
  return {
    title: pickTitle(item),
    body: pickBody(item),
    tags: badges.map((b) => b.text),
    badges,
    raw: item,
  };
}

/** 从 envelope 摊平为通用展示项列表（兼容 aggregate.mixed_results 与常规 results / relations / paragraphs）。 */
export function buildQueryItems(payload: QueryResult): QueryDisplayItem[] {
  let items: QueryResultItem[] = [];
  if (Array.isArray(payload?.mixed_results)) {
    items = payload.mixed_results;
  } else if (Array.isArray(payload?.results)) {
    items = payload.results;
  } else if (Array.isArray(payload?.relations)) {
    items = payload.relations;
  } else if (Array.isArray(payload?.paragraphs)) {
    items = payload.paragraphs;
  } else if (Array.isArray(payload?.neighbors)) {
    items = payload.neighbors.map((x) => ({ type: "neighbor", title: x }));
  } else if (payload?.branches && typeof payload.branches === "object") {
    // 当 aggregate mix=false 时兜底汇总各分支结果
    for (const key of ["search", "time", "episode"]) {
      const branchResults = payload.branches[key]?.results;
      if (Array.isArray(branchResults)) {
        items.push(...branchResults.map((r) => ({ ...r, source_branches: [key] })));
      }
    }
  }
  return items.map((item) => normalizeDisplayItem(item, payload));
}

/** 为 entity 模式构造完整的三分区数据（关联关系 + 提及段落 + 邻接实体）。 */
export function buildEntitySections(payload: QueryResult | null): EntityDetailSections | null {
  if (!payload) return null;
  const hasEntityShape =
    Array.isArray(payload.relations) ||
    Array.isArray(payload.paragraphs) ||
    Array.isArray(payload.neighbors);
  if (!hasEntityShape) return null;

  const relations = (payload.relations || []).map((rel) =>
    normalizeDisplayItem({ ...rel, type: "relation" }, payload),
  );
  const paragraphs = (payload.paragraphs || []).map((para) =>
    normalizeDisplayItem({ ...para, type: "paragraph" }, payload),
  );
  const neighbors = Array.isArray(payload.neighbors)
    ? payload.neighbors.map((n) => str(n)).filter(Boolean)
    : [];

  return {
    entityName: str(payload.entity_name || payload.query),
    relations,
    paragraphs,
    neighbors,
  };
}

/** 提取聚合模式的三路分支诊断徽标。 */
export function deriveAggregateBranchChips(payload: QueryResult | null): AggregateBranchChip[] {
  if (!payload || !payload.branches) return [];
  const skipReasonMap: Record<string, string> = {
    missing_query: "未填关键词已跳过",
    missing_time_window: "未设时间窗已跳过",
  };
  const order: Array<{ key: string; label: string }> = [
    { key: "search", label: "语义检索" },
    { key: "time", label: "时序检索" },
    { key: "episode", label: "情景检索" },
  ];

  return order
    .filter(({ key }) => payload.branches && key in payload.branches)
    .map(({ key, label }) => {
      const b = payload.branches?.[key] || {};
      const count = Number(b.count ?? b.results?.length ?? 0);
      if (b.skipped) {
        const reason = skipReasonMap[str(b.skip_reason)] || str(b.skip_reason) || "已跳过";
        return { key, label, detail: reason, tone: "" };
      }
      if (b.success === false || b.status === "failed") {
        return {
          key,
          label,
          detail: b.error ? `失败：${str(b.error)}` : "执行失败",
          tone: "bad",
        };
      }
      const ms = b.elapsed_ms ? ` · ${Math.round(Number(b.elapsed_ms))}ms` : "";
      return {
        key,
        label,
        detail: `命中 ${count} 条${ms}`,
        tone: count > 0 ? "ok" : "",
      };
    });
}

/** 计算查询结果总数并生成中文 meta 文案。 */
export function queryMetaLabel(mode: string, payload: QueryResult): string {
  const label = queryModeLabel(mode);
  if (mode === "entity") {
    const r = payload.relations?.length ?? 0;
    const p = payload.paragraphs?.length ?? 0;
    const nb = payload.neighbors?.length ?? 0;
    return `${label} · ${r} 条关系 / ${p} 条段落 / ${nb} 个邻接实体`;
  }
  const n =
    payload.mixed_results?.length ??
    payload.count ??
    payload.results?.length ??
    payload.relations?.length ??
    0;
  const elapsed =
    payload.elapsed_ms != null && Number.isFinite(Number(payload.elapsed_ms))
      ? ` · ${Math.round(Number(payload.elapsed_ms))}ms`
      : "";
  return `${label} · 共 ${n} 条结果${elapsed}`;
}

/** 截断正文用于列表预览。 */
export function truncateBody(body: string, max = 900): string {
  return body.length > max ? `${body.slice(0, max)}…` : body;
}
