import type { Episode } from "@/services/episodeApi";
import { formatTs } from "./time";

/** episode 列表项标题（title 优先，回退 episode_id）。 */
export function episodeTitle(ep: Episode | null | undefined): string {
  if (!ep) return "";
  return ep.title || ep.episode_id || "episode";
}

/** episode 摘要展示（summary 优先，回退 content）。 */
export function episodeSummary(ep: Episode | null | undefined): string {
  if (!ep) return "";
  return ep.summary || ep.content || "";
}

/** episode 来源标签。 */
export function episodeSourceLabel(ep: Episode | null | undefined): string {
  return ep?.source || "source";
}

/** 段落数标签。 */
export function episodeParagraphCountLabel(ep: Episode | null | undefined): string {
  const n = ep?.paragraph_count ?? 0;
  return `${n} paragraphs`;
}

/** 关键词（最多 5 个）。 */
export function episodeKeywords(ep: Episode | null | undefined): string[] {
  return Array.isArray(ep?.keywords) ? ep.keywords.slice(0, 5) : [];
}

/** 参与者列表。 */
export function episodeParticipants(ep: Episode | null | undefined): string[] {
  return Array.isArray(ep?.participants) ? ep.participants : [];
}

/** 事件时间区间展示。 */
export function episodeTimeRange(ep: Episode | null | undefined): string {
  if (!ep) return "-";
  const start = ep.event_time_start != null ? formatTs(ep.event_time_start) : "";
  const end = ep.event_time_end != null ? formatTs(ep.event_time_end) : "";
  if (start && end && start !== end) return `${start} ~ ${end}`;
  return start || end || "-";
}

/** episode 重建结果摘要文本。 */
export function rebuildResultText(r: {
  episode_count?: number;
  fallback_count?: number;
  group_count?: number;
  paragraph_count?: number;
} | null): string {
  if (!r) return "";
  const parts: string[] = [];
  if (r.episode_count != null) parts.push(`${r.episode_count} episodes`);
  if (r.fallback_count != null) parts.push(`${r.fallback_count} fallback`);
  if (r.group_count != null) parts.push(`${r.group_count} groups`);
  if (r.paragraph_count != null) parts.push(`${r.paragraph_count} paragraphs`);
  return parts.join(" · ") || "完成";
}

/** 从 task detail 推断可读状态标签。 */
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
    completed_with_errors: "完成（有错误）",
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
