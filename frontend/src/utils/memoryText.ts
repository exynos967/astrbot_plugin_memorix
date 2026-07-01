// Memory 文本派生工具：从 legacy formatMemoryHours（index.html 行 4555-4561）+ renderMemoryStatus
// KPI/config 构建（行 4563-4586）+ recycleTitle/type 推断（行 4635-4644）移植为纯函数。

import type { MemoryStatus, RecycleItem } from "@/services/memoryApi";

/** 小时数格式化（天/小时）。 */
export function formatMemoryHours(value: unknown): string {
  const hours = Number(value);
  if (!Number.isFinite(hours)) return "-";
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

/** 从 status 派生 4 个 KPI（与 legacy renderMemoryStatus 一致）。 */
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
    { label: "受保护", value: pinned + ttl, note: `${pinned} 置顶 / ${ttl} 限时` },
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

/** 回收站项标题（关系优先 subject→object，否则 name/hash）。 */
export function recycleTitle(item: RecycleItem): string {
  if (item.subject && item.object) return `${item.subject} → ${item.object}`;
  return item.name || item.hash || "记忆";
}

/** 推断回收站项类型（relation/entity）。 */
export function recycleType(item: RecycleItem): string {
  return item.type || (item.subject && item.object ? "relation" : "entity");
}

/** 回收站项删除时间格式化。 */
export function recycleDeletedAt(item: RecycleItem): string {
  return item.deleted_at
    ? new Date(Number(item.deleted_at) * 1000).toLocaleString("zh-CN", { hour12: false })
    : "-";
}

export function recycleDetail(item: RecycleItem): string {
  return item.predicate || item.content || item.name || "";
}
