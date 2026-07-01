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
  return (
    item.display_name ||
    item.person_name ||
    item.nickname ||
    item.user_id ||
    item.person_id ||
    ""
  );
}

/** 取别名列数组（空安全）。 */
export function personAliases(item: { aliases?: string[] } | null | undefined): string[] {
  return Array.isArray(item?.aliases) ? item.aliases.filter(Boolean) : [];
}

/** 画像标题。 */
export function profileTitle(profile: PersonProfile | null): string {
  if (!profile) return "";
  return profile.person_name || profile.person_id || "人物画像";
}

/** 画像来源标签。 */
export function profileSourceLabel(profile: PersonProfile | null): string {
  const s = profile?.profile_source;
  if (s === "manual_override") return "人工覆盖";
  if (s === "auto_snapshot") return "自动画像";
  return s || "-";
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
  return Array.isArray(profile?.vector_evidence) ? profile.vector_evidence.length : 0;
}

/**
 * 从 registry 列表提取候选选项（每人仅取最佳展示名，跨 item 去重，过滤 dict 垃圾值）。
 * 替代原先 `items.flatMap(personCandidateValues)` 的"一人多条目"爆炸模式。
 */
export function personCandidateValues(items: PersonRegistryItem[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of items) {
    // 优先 display_name，回退各别名维度
    const raw = item.display_name || item.person_name || item.nickname || item.person_id || "";
    if (!raw || isGarbageValue(raw)) continue;
    if (seen.has(raw)) continue;
    seen.add(raw);
    result.push(raw);
  }
  return result;
}
