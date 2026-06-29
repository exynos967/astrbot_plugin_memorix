import type {
  SourceListItem,
  SourceListResult,
  SourceParagraphItem,
  SourceSummaryItem,
} from "@/services/sourceApi";
import { formatTs } from "./time";

/** 取来源名（兼容字符串项与对象项）。 */
export function sourceNameOf(item: SourceListItem): string {
  if (typeof item === "string") return item;
  return (item as SourceSummaryItem)?.source || "";
}

/** 是否为段落项（有 hash）。 */
export function isParagraph(item: SourceListItem): item is SourceParagraphItem {
  return typeof item === "object" && item !== null && "hash" in item && !!item.hash;
}

/** 是否 summary 聚合模式。 */
export function isSummaryMode(result: SourceListResult | null): boolean {
  return result?.mode === "summary";
}

export function paragraphHashShort(hash: string | undefined): string {
  return hash ? String(hash).slice(0, 16) : "";
}

export function sourceCountLabel(item: SourceSummaryItem): string {
  return item.count != null ? `${item.count} 段` : "";
}

export function sourceUpdatedLabel(item: SourceSummaryItem): string {
  return item.last_updated != null ? formatTs(item.last_updated) : "";
}
