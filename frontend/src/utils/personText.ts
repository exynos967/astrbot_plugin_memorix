import type {
  PersonProfile,
  PersonRegistryItem,
} from "@/services/personApi";

/** registry 列表项的展示名（优先 display_name，回退各别名维度）。 */
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

/** registry item 可供候选菜单使用的候选值集合（person_id + display + 各别名，去重）。 */
export function personCandidateValues(item: PersonRegistryItem): string[] {
  const set: string[] = [];
  const values = [
    item.person_id,
    item.display_name,
    item.person_name,
    item.nickname,
    item.user_id,
    ...(item.aliases || []),
  ];
  for (const v of values) {
    if (v && !set.includes(v)) set.push(v);
  }
  return set;
}
