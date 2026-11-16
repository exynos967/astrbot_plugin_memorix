import type { CandidateItem } from "@/stores/candidate";
import type {
  PersonProfile,
  PersonRegistryItem,
} from "@/services/personApi";

/** 过滤 Python dict/JSON 字符串、超长值等脏数据。 */
function isGarbageValue(v: string): boolean {
  const s = v.trim();
  return s.startsWith("{") || s.startsWith("[") || s.length > 200;
}

/** registry 列表项的展示名（优先 display_name，回退各别名维度）。
 *  过滤 dict 字符串等垃圾值。 */
export function personDisplayName(item: PersonRegistryItem): string {
  const candidates = [
    item.display_name,
    item.person_name,
    item.nickname,
    item.user_id,
    item.person_id,
  ];
  for (const c of candidates) {
    const text = String(c || "").trim();
    if (text && !isGarbageValue(text)) return text;
  }
  return String(item.person_id || "").trim();
}

/** 取别名列数组（空安全，过滤脏值）。 */
export function personAliases(item: { aliases?: string[] } | null | undefined): string[] {
  if (!Array.isArray(item?.aliases)) return [];
  return item.aliases.map((a) => String(a || "").trim()).filter((a) => a && !isGarbageValue(a));
}

/** 画像标题。 */
export function profileTitle(profile: PersonProfile | null): string {
  if (!profile) return "";
  return profile.person_name || profile.person_id || "人物画像";
}

/** 画像来源标签。 */
export function profileSourceLabel(profile: PersonProfile | null): string {
  if (!profile) return "-";
  if (profile.has_manual_override || profile.profile_source === "manual_override") {
    return "人工覆盖";
  }
  const s = profile.profile_source;
  if (s === "auto_snapshot" || !s) return "自动画像";
  return s;
}

/** 人工覆盖文本（兼容 manual_override_text / override_text 两种字段）。 */
export function profileOverrideText(profile: PersonProfile | null): string {
  if (!profile) return "";
  return profile.manual_override_text || profile.override_text || "";
}

export function profileRelationCount(profile: PersonProfile | null): number {
  return Array.isArray(profile?.relation_edges) ? profile.relation_edges.length : 0;
}

export function profileEvidenceCount(profile: PersonProfile | null): number {
  if (Array.isArray(profile?.vector_evidence) && profile.vector_evidence.length > 0) {
    return profile.vector_evidence.length;
  }
  return Array.isArray(profile?.evidence_ids) ? profile.evidence_ids.length : 0;
}

/**
 * 从 registry 列表提取关键词候选选项（供查询框使用：每人仅取最佳展示名，跨 item 去重，过滤 dict 垃圾值）。
 */
export function personCandidateValues(items: PersonRegistryItem[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of items) {
    const raw = (
      item.display_name ||
      item.person_name ||
      item.nickname ||
      item.person_id ||
      ""
    ).trim();
    if (!raw || isGarbageValue(raw)) continue;
    if (seen.has(raw)) continue;
    seen.add(raw);
    result.push(raw);
  }
  return result;
}

export interface PersonIdCandidateItem extends CandidateItem {
  value: string;
  label: string;
  hint: string;
  kind: string;
}

/**
 * 从 registry 列表提取规范化 person_id 候选项（供人工覆盖/精确 ID 绑定使用）。
 * value 恒为规范 person_id，绝不混入 display_name，防止覆盖写错主键；
 * label / hint / kind 展示「display_name · platform · person_id」便于辨识。
 */
export function personIdCandidateItems(items: PersonRegistryItem[]): PersonIdCandidateItem[] {
  const seen = new Set<string>();
  const result: PersonIdCandidateItem[] = [];
  for (const item of items) {
    const pid = String(item.person_id || "").trim();
    if (!pid || isGarbageValue(pid) || seen.has(pid)) continue;
    seen.add(pid);

    const displayName = personDisplayName(item);
    const platform = String(item.platform || "").trim();
    const fullParts = [
      displayName && displayName !== pid ? displayName : "",
      platform,
      pid,
    ].filter(Boolean);
    const summary = fullParts.join(" · ") || pid;

    const kindParts = [
      displayName && displayName !== pid ? displayName : "",
      platform,
    ].filter(Boolean);

    result.push({
      value: pid,
      label: summary,
      hint: summary,
      kind: kindParts.join(" · ") || "person_id",
    });
  }
  return result;
}

/** A_Memorix v2.0 人物事实画像 6 大标准分区。 */
export const FACT_PROFILE_SECTIONS = [
  { value: "identity_settings", label: "身份设定" },
  { value: "relationship_settings", label: "关系设定" },
  { value: "stable_facts", label: "稳定了解" },
  { value: "interaction_preferences", label: "相处偏好" },
  { value: "recent_interactions", label: "近期互动" },
  { value: "uncertain_notes", label: "不确定信息" },
] as const;

export const FACT_POLARITY_OPTIONS = [
  { value: "positive", label: "肯定（是 / 具有）" },
  { value: "negative", label: "否定（不 / 排斥）" },
] as const;

export const FACT_STABILITY_OPTIONS = [
  { value: "stable", label: "稳定（长期）" },
  { value: "temporal", label: "阶段性（近期）" },
  { value: "uncertain", label: "待确认（存疑）" },
] as const;

export const FACT_CARDINALITY_OPTIONS = [
  { value: "set", label: "可并存多条（集合 set）" },
  { value: "single", label: "唯一单值（互斥 single）" },
] as const;

export function factSectionLabel(section: string | undefined): string {
  const found = FACT_PROFILE_SECTIONS.find((s) => s.value === section);
  return found ? found.label : section || "稳定了解";
}

export function factStabilityLabel(stability: string | undefined): string {
  const map: Record<string, string> = {
    stable: "稳定",
    temporal: "阶段性",
    uncertain: "待确认",
  };
  return (stability && map[stability]) || stability || "稳定";
}

export function factStatusLabel(status: string | undefined): string {
  const map: Record<string, string> = {
    active: "生效中",
    conflicted: "冲突待决",
    superseded: "已被替代",
    retracted: "已撤回",
  };
  return (status && map[status]) || status || "未知";
}

export function evidenceTypeLabel(type: string | undefined): string {
  const map: Record<string, string> = {
    relation: "知识关系边",
    paragraph: "记忆段落",
    person_fact: "人物事实",
    chat_summary: "对话摘要",
  };
  return (type && map[type]) || type || "证据";
}
