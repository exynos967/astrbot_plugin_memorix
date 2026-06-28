// 配置表单字段定义：从 legacy configFields（index.html 行 3021-3071）忠实迁移。
// 5 组（运行与任务 / 检索策略 / 记忆衰减 / Episode / 人物画像），与后端 _conf_schema 对应。
// 纯数据常量，供 SettingsView 渲染表单 + 收集更新（DRY）。

export type ConfigFieldType = "boolean" | "number";

export interface ConfigField {
  key: string;
  label: string;
  type: ConfigFieldType;
  min?: number;
  max?: number;
  step?: number;
  default: number | boolean;
}

export interface ConfigFieldGroup {
  title: string;
  fields: ConfigField[];
}

export const CONFIG_FIELDS: ConfigFieldGroup[] = [
  {
    title: "运行与任务",
    fields: [
      { key: "advanced.enable_auto_save", label: "自动保存", type: "boolean", default: true },
      { key: "advanced.auto_save_interval_minutes", label: "自动保存间隔（分钟）", type: "number", min: 0.1, max: 1440, step: 0.1, default: 5 },
      { key: "advanced.debug", label: "调试日志", type: "boolean", default: false },
      { key: "tasks.queue_maxsize", label: "任务队列容量", type: "number", min: 1, max: 100000, step: 1, default: 1024 },
    ],
  },
  {
    title: "检索策略",
    fields: [
      { key: "retrieval.top_k_paragraphs", label: "段落召回数", type: "number", min: 1, max: 200, step: 1, default: 20 },
      { key: "retrieval.top_k_relations", label: "关系召回数", type: "number", min: 1, max: 200, step: 1, default: 10 },
      { key: "retrieval.top_k_final", label: "最终结果数", type: "number", min: 1, max: 200, step: 1, default: 10 },
      { key: "retrieval.alpha", label: "语义权重 alpha", type: "number", min: 0, max: 1, step: 0.05, default: 0.5 },
      { key: "retrieval.enable_ppr", label: "启用 PPR", type: "boolean", default: true },
      { key: "retrieval.ppr_alpha", label: "PPR alpha", type: "number", min: 0, max: 1, step: 0.05, default: 0.85 },
      { key: "retrieval.ppr_timeout_seconds", label: "PPR 超时（秒）", type: "number", min: 0.1, max: 60, step: 0.1, default: 1.5 },
    ],
  },
  {
    title: "记忆衰减",
    fields: [
      { key: "memory.enabled", label: "启用记忆维护", type: "boolean", default: true },
      { key: "memory.half_life_hours", label: "半衰期（小时）", type: "number", min: 0.1, max: 8760, step: 0.1, default: 24 },
      { key: "memory.prune_threshold", label: "剪枝阈值", type: "number", min: 0, max: 100, step: 0.01, default: 0.1 },
      { key: "memory.auto_protect_ttl_hours", label: "自动保护 TTL（小时）", type: "number", min: 0, max: 8760, step: 0.1, default: 24 },
    ],
  },
  {
    title: "Episode",
    fields: [
      { key: "episode.enabled", label: "启用 Episode", type: "boolean", default: true },
      { key: "episode.generation_enabled", label: "自动生成", type: "boolean", default: true },
      { key: "episode.generation_interval_seconds", label: "生成间隔（秒）", type: "number", min: 1, max: 86400, step: 1, default: 30 },
      { key: "episode.generation_batch_size", label: "生成批量", type: "number", min: 1, max: 1000, step: 1, default: 20 },
      { key: "episode.max_retry", label: "最大重试", type: "number", min: 0, max: 20, step: 1, default: 3 },
    ],
  },
  {
    title: "人物画像",
    fields: [
      { key: "person_profile.enabled", label: "启用画像", type: "boolean", default: true },
      { key: "person_profile.profile_ttl_minutes", label: "画像 TTL（分钟）", type: "number", min: 1, max: 525600, step: 1, default: 360 },
      { key: "person_profile.refresh_interval_minutes", label: "刷新间隔（分钟）", type: "number", min: 1, max: 10080, step: 1, default: 30 },
      { key: "person_profile.top_k_evidence", label: "证据 TopK", type: "number", min: 1, max: 100, step: 1, default: 12 },
    ],
  },
];

/** 读取点分键的值（从 legacy pathValue 行 3073-3078 迁移）。 */
export function pathValue(root: unknown, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>((current, part) => {
      if (current && typeof current === "object" && part in (current as object)) {
        return (current as Record<string, unknown>)[part];
      }
      return undefined;
    }, root);
}

/** 读取字段当前值（配置缺失时回退 default；number 缺失回退 min）。 */
export function fieldValue(
  config: Record<string, unknown> | undefined,
  field: ConfigField,
): number | boolean {
  const fallback =
    field.default ?? (field.type === "boolean" ? false : field.min ?? "");
  const raw = pathValue(config, field.key);
  if (raw === undefined || raw === null) return fallback;
  if (field.type === "boolean") return raw === true || raw === "true";
  const num = Number(raw);
  return Number.isFinite(num) ? num : (field.min ?? 0);
}
