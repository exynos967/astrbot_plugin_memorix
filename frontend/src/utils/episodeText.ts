import type { Episode, EpisodeRebuildItem, EpisodeRebuildResult } from "@/services/episodeApi";
import { formatTs } from "./time";

/** episode 列表项标题（title 优先，回退 episode_id）。 */
export function episodeTitle(ep: Episode | null | undefined): string {
  if (!ep) return "";
  return ep.title || ep.episode_id || "未命名情景";
}

/** episode 摘要展示（summary 优先，回退 content）。 */
export function episodeSummary(ep: Episode | null | undefined): string {
  if (!ep) return "";
  return String(ep.summary || ep.content || "").trim();
}

/** episode 列表摘要短预览。 */
export function episodeSummaryPreview(ep: Episode | null | undefined, max = 110): string {
  const raw = episodeSummary(ep).replace(/\s+/g, " ");
  if (!raw) return "";
  return raw.length > max ? `${raw.slice(0, max)}…` : raw;
}

/** episode 来源标签（中文兜底）。 */
export function episodeSourceLabel(ep: Episode | null | undefined): string {
  return ep?.source || "默认来源";
}

/** 段落数中文标签。 */
export function episodeParagraphCountLabel(ep: Episode | null | undefined): string {
  const n = ep?.paragraph_count ?? ep?.paragraphs?.length ?? 0;
  return `${n} 条段落`;
}

/** 时间粒度中文标签。 */
export function episodeGranularityLabel(granularity: string | null | undefined): string {
  if (!granularity) return "";
  const map: Record<string, string> = {
    exact: "精确时刻",
    minute: "分钟级",
    hour: "小时级",
    day: "按天",
    week: "按周",
    month: "按月",
    year: "按年",
    range: "时间段",
    approximate: "模糊时间",
  };
  const key = String(granularity).toLowerCase();
  return map[key] || granularity;
}

/** 段落类型中文标签。 */
export function paragraphTypeLabel(type: string | undefined): string {
  if (!type) return "";
  const map: Record<string, string> = {
    paragraph: "原始段落",
    chat: "对话记录",
    summary: "会话摘要",
    document: "文档切片",
    narrative: "叙事片段",
  };
  return map[String(type).toLowerCase()] || type;
}

/** 关键词（最多 8 个）。 */
export function episodeKeywords(ep: Episode | null | undefined): string[] {
  return Array.isArray(ep?.keywords) ? ep.keywords.slice(0, 8) : [];
}

/** 参与者列表。 */
export function episodeParticipants(ep: Episode | null | undefined): string[] {
  return Array.isArray(ep?.participants) ? ep.participants : [];
}

/** 事件时间区间展示。 */
export function episodeTimeRange(ep: Episode | null | undefined): string {
  if (!ep) return "未记录时间";
  const start = ep.event_time_start != null ? formatTs(ep.event_time_start) : "";
  const end = ep.event_time_end != null ? formatTs(ep.event_time_end) : "";
  if (start && end && start !== end) return `${start} ~ ${end}`;
  return start || end || "未记录时间";
}

/** 汇总单次或批量重建的统计指标（含 v2.0 缓存复用指标）。 */
export function deriveRebuildMetrics(
  r: (EpisodeRebuildResult & Record<string, unknown>) | null | undefined,
): {
  summaryText: string;
  chips: Array<{ label: string; tone?: "" | "ok" | "warn" | "bad" }>;
  failures: Array<{ source: string; error: string }>;
} {
  if (!r) return { summaryText: "", chips: [], failures: [] };

  const items = Array.isArray(r.items) ? (r.items as EpisodeRebuildItem[]) : [];
  const sumField = (key: keyof EpisodeRebuildItem): number | null => {
    if (r[key] != null && Number.isFinite(Number(r[key]))) return Number(r[key]);
    if (!items.length) return null;
    let found = false;
    const total = items.reduce((acc, it) => {
      if (it && it[key] != null && Number.isFinite(Number(it[key]))) {
        found = true;
        return acc + Number(it[key]);
      }
      return acc;
    }, 0);
    return found ? total : null;
  };

  const episodeCount = sumField("episode_count");
  const groupCount = sumField("group_count");
  const paragraphCount = sumField("paragraph_count");
  const fallbackCount = sumField("fallback_count");
  const reusedGroupCount = sumField("reused_group_count");
  const reusedEpisodeCount = sumField("reused_episode_count");
  const rebuiltSources =
    r.rebuilt != null
      ? Number(r.rebuilt)
      : items.length > 0
        ? items.length
        : null;

  const chips: Array<{ label: string; tone?: "" | "ok" | "warn" | "bad" }> = [];
  if (rebuiltSources != null) chips.push({ label: `完成来源 ${rebuiltSources}`, tone: "ok" });
  if (episodeCount != null) chips.push({ label: `生成情景 ${episodeCount} 条`, tone: "ok" });
  if (groupCount != null) chips.push({ label: `事件分组 ${groupCount} 组` });
  if (paragraphCount != null) chips.push({ label: `覆盖段落 ${paragraphCount} 段` });
  if (reusedGroupCount != null) {
    chips.push({ label: `缓存复用分组 ${reusedGroupCount}`, tone: reusedGroupCount > 0 ? "ok" : "" });
  }
  if (reusedEpisodeCount != null) {
    chips.push({ label: `缓存复用情景 ${reusedEpisodeCount}`, tone: reusedEpisodeCount > 0 ? "ok" : "" });
  }
  if (fallbackCount != null && fallbackCount > 0) {
    chips.push({ label: `降级回退 ${fallbackCount} 条`, tone: "warn" });
  }
  if (r.unfinished != null && Number(r.unfinished) > 0) {
    chips.push({ label: `未完成 ${Number(r.unfinished)}`, tone: "warn" });
  }
  if (r.processed != null) {
    chips.push({ label: `处理待生成项 ${Number(r.processed)}`, tone: "ok" });
  }
  if (r.discarded != null) {
    chips.push({ label: `清理迁移积压 ${Number(r.discarded)} 项`, tone: "ok" });
  }

  const rawFailures = Array.isArray(r.failures) ? r.failures : [];
  const failures = rawFailures.map((f) => ({
    source: String(f?.source || "未知来源"),
    error: String(f?.error || "重建失败"),
  }));
  if (failures.length > 0) {
    chips.push({ label: `失败 ${failures.length} 项`, tone: "bad" });
  }

  const summaryText = chips.map((c) => c.label).join(" · ") || "操作已完成";
  return { summaryText, chips, failures };
}

/** 兼容旧调用的纯文本重建摘要。 */
export function rebuildResultText(r: Record<string, unknown> | null): string {
  return deriveRebuildMetrics(r as EpisodeRebuildResult | null).summaryText;
}

/** 从 task detail 推断可读状态标签（供 ImportView / TaskStatusPanel 复用）。 */
export function taskStatusLabel(status: string | undefined): string {
  const map: Record<string, string> = {
    queued: "排队中",
    waiting: "等待中",
    preparing: "准备中",
    running: "运行中",
    writing: "写入中",
    splitting: "切分中",
    succeeded: "已完成",
    completed: "已完成",
    completed_with_errors: "完成（含错误）",
    failed: "失败",
    canceled: "已取消",
    cancelled: "已取消",
    cancel_requested: "取消中",
  };
  return (status && map[status]) || status || "未知";
}

/** 任务进度百分比（兼容 progress 0-1 与 chunk 计数）。 */
export function taskProgressPct(task: {
  progress?: number;
  done_chunks?: number;
  total_chunks?: number;
} | null): string {
  if (!task) return "-";
  if (typeof task.progress === "number") return `${Math.round(task.progress * 100)}%`;
  if (task.total_chunks) {
    const done = task.done_chunks ?? 0;
    return `${Math.round((done / task.total_chunks) * 100)}% (${done}/${task.total_chunks})`;
  }
  return "-";
}
