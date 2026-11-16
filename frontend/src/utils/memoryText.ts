// Memory 文本与枚举派生工具：KPI、配置项、回收站三类型（relation / entity / paragraph）、
// 删除操作模式/状态中文映射、以及 Schema v31 结构化事实台账标准枚举中文映射。

import type { MemoryStatus, RecycleItem } from "@/services/memoryApi";

/** 小时数格式化（天/小时）。 */
export function formatMemoryHours(value: unknown): string {
  const hours = Number(value);
  if (!Number.isFinite(hours)) return "-";
  if (hours <= 0) return "永久";
  if (hours >= 24 && hours % 24 === 0) return `${hours / 24} 天`;
  if (hours >= 24) return `${(hours / 24).toFixed(1)} 天`;
  return `${hours} 小时`;
}

export interface MemoryKpi {
  label: string;
  value: number | string;
  note: string;
}

export interface MemoryConfigItem {
  label: string;
  value: string;
}

/** 从 status 派生 4 个 KPI。 */
export function deriveMemoryKpis(status: MemoryStatus | null): MemoryKpi[] {
  if (!status) return [];
  const active = Number(status.active_relations || 0);
  const inactive = Number(status.inactive_relations || 0);
  const recycle = Number(status.recycle_bin_relations || 0);
  const pinned = Number(status.pinned_relations || 0);
  const ttl = Number(status.ttl_protected_relations || 0);
  return [
    { label: "活跃关系", value: active, note: "参与检索与图谱计算" },
    { label: "冷冻关系", value: inactive, note: "暂不参与图谱计算" },
    { label: "回收站", value: recycle, note: "可恢复的删除记忆" },
    { label: "受保护", value: pinned + ttl, note: `${pinned} 永久置顶 / ${ttl} 限时保护` },
  ];
}

/** 从 status.config 派生配置展示项。 */
export function deriveMemoryConfigs(status: MemoryStatus | null): MemoryConfigItem[] {
  const cfg = (status?.config || {}) as Record<string, unknown>;
  return [
    { label: "记忆系统", value: cfg.enabled === false ? "关闭" : "开启" },
    { label: "半衰期", value: formatMemoryHours(cfg.half_life_hours) },
    { label: "衰减周期", value: formatMemoryHours(cfg.base_decay_interval_hours) },
    { label: "冷冻时长", value: formatMemoryHours(cfg.freeze_duration_hours) },
    { label: "剪枝阈值", value: String(cfg.prune_threshold ?? "-") },
    { label: "自动强化", value: cfg.enable_auto_reinforce === false ? "关闭" : "开启" },
    { label: "强化缓冲", value: String(cfg.reinforce_buffer_max_size ?? "-") },
    { label: "自动保护", value: formatMemoryHours(cfg.auto_protect_ttl_hours) },
  ];
}

/** 推断回收站项类型（支持 relation / entity / paragraph）。 */
export function recycleType(item: RecycleItem): "relation" | "entity" | "paragraph" {
  const raw = String(item.type || "").toLowerCase();
  if (raw === "relation" || raw === "entity" || raw === "paragraph") return raw;
  if (item.subject && item.object) return "relation";
  if (item.content && !item.name) return "paragraph";
  return "entity";
}

export function recycleTypeLabel(type: string): string {
  if (type === "relation") return "关系";
  if (type === "paragraph") return "段落";
  return "实体";
}

/** 回收站项标题。 */
export function recycleTitle(item: RecycleItem): string {
  const kind = recycleType(item);
  if (item.subject && item.object) {
    return item.predicate
      ? `${item.subject} —[${item.predicate}]→ ${item.object}`
      : `${item.subject} → ${item.object}`;
  }
  if (kind === "paragraph") {
    if (item.source) return `段落 · ${item.source}`;
    return item.hash ? `段落 ${String(item.hash).slice(0, 12)}` : "已删除段落";
  }
  return item.name || item.hash || "已删除实体";
}

/** 回收站项删除时间格式化。 */
export function recycleDeletedAt(item: RecycleItem): string {
  return item.deleted_at
    ? new Date(Number(item.deleted_at) * 1000).toLocaleString("zh-CN", { hour12: false })
    : "-";
}

export function recycleDetail(item: RecycleItem): string {
  if (item.subject && item.object) return item.content || "";
  return item.content || item.predicate || "";
}

/** 删除操作模式中文文案。 */
const DELETE_MODE_LABELS: Record<string, string> = {
  paragraph: "按段落",
  entity: "按实体",
  relation: "按关系",
  source: "按来源批次",
  clear: "全量清空",
};

export function deleteModeLabel(mode: string | undefined): string {
  const key = String(mode || "").toLowerCase();
  return DELETE_MODE_LABELS[key] || mode || "未知模式";
}

/** 删除操作状态中文文案与标签色调。 */
export function deleteStatusMeta(
  status: string | undefined,
): { label: string; tone: "" | "ok" | "warn" | "bad" } {
  const key = String(status || "").toLowerCase();
  if (key === "executed") return { label: "可恢复", tone: "warn" };
  if (key === "restored") return { label: "已恢复", tone: "ok" };
  if (key === "purged") return { label: "已清理归档", tone: "" };
  if (key === "failed") return { label: "失败", tone: "bad" };
  return { label: status || "未知状态", tone: "" };
}

/** Schema v31 事实台账 6 大标准画像分区。 */
export const FACT_PROFILE_SECTIONS: Array<{ value: string; label: string }> = [
  { value: "identity_settings", label: "身份设定" },
  { value: "relationship_settings", label: "关系设定" },
  { value: "stable_facts", label: "稳定事实" },
  { value: "interaction_preferences", label: "互动偏好" },
  { value: "recent_interactions", label: "近期动态" },
  { value: "uncertain_notes", label: "存疑备注" },
];

export const FACT_STABILITY_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "stable", label: "长期稳定" },
  { value: "temporal", label: "阶段时效" },
  { value: "uncertain", label: "待确认/存疑" },
];

export const FACT_CARDINALITY_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "set", label: "多值集合 (set)" },
  { value: "single", label: "单值互斥 (single)" },
];

export const FACT_AUTHORITY_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "manual", label: "人工录入" },
  { value: "direct_user", label: "用户直述" },
  { value: "imported", label: "外部导入" },
  { value: "summary_derived", label: "摘要归纳" },
];

export const FACT_STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "全部状态" },
  { value: "active", label: "生效中" },
  { value: "conflicted", label: "存在冲突" },
  { value: "superseded", label: "已被更替" },
  { value: "retracted", label: "已撤回" },
];

export function factSectionLabel(section: string | undefined): string {
  const found = FACT_PROFILE_SECTIONS.find((item) => item.value === section);
  return found ? found.label : section || "未分区";
}

export function factStabilityLabel(stability: string | undefined): string {
  const found = FACT_STABILITY_OPTIONS.find((item) => item.value === stability);
  return found ? found.label : stability || "-";
}

export function factCardinalityLabel(cardinality: string | undefined): string {
  if (cardinality === "single") return "单值";
  if (cardinality === "set") return "多值";
  return cardinality || "-";
}

export function factAuthorityLabel(authority: string | undefined): string {
  const found = FACT_AUTHORITY_OPTIONS.find((item) => item.value === authority);
  return found ? found.label : authority || "-";
}

export function factStatusMeta(
  status: string | undefined,
): { label: string; tone: "" | "ok" | "warn" | "bad" } {
  const key = String(status || "").toLowerCase();
  if (key === "active") return { label: "生效中", tone: "ok" };
  if (key === "conflicted") return { label: "冲突待决", tone: "warn" };
  if (key === "superseded") return { label: "已更替", tone: "" };
  if (key === "retracted") return { label: "已撤回", tone: "bad" };
  return { label: status || "未知", tone: "" };
}
