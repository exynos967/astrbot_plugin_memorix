<script setup lang="ts">
// 来源/段落列表渲染：扁平 1px 细线行列表 + 缩进细线展开抽屉（移除 4 层盒套盒），统一按钮层级。
import type {
  SourceListItem,
  SourceParagraphItem,
  SourceSummaryItem,
} from "@/services/sourceApi";
import {
  isParagraph,
  paragraphCreatedLabel,
  paragraphHashShort,
  sourceCountLabel,
  sourceNameOf,
  sourceUpdatedLabel,
} from "@/utils/sourceText";

const props = defineProps<{
  items: SourceListItem[];
  loading?: boolean;
  deleting?: boolean;
  summaryMode?: boolean;
  expandedSource?: string;
  expandedParagraphs?: SourceParagraphItem[];
  loadingExpanded?: boolean;
}>();

const emit = defineEmits<{
  toggleExpand: [source: string];
  focusGraph: [source: string];
  removeSource: [source: string];
  removeParagraph: [hash: string];
}>();

function summaryTags(item: SourceSummaryItem): string[] {
  return [sourceCountLabel(item), sourceUpdatedLabel(item)].filter(Boolean);
}

function keyOf(item: SourceListItem, idx: number): string {
  if (isParagraph(item)) return item.hash || `p${idx}`;
  return sourceNameOf(item) || `s${idx}`;
}

function paragraphTitle(item: SourceParagraphItem): string {
  return item.source ? `来源：${item.source}` : `段落 ${paragraphHashShort(item.hash)}`;
}

function isExpanded(item: SourceListItem): boolean {
  const src = sourceNameOf(item);
  return Boolean(src && props.expandedSource === src);
}

function onToggleRow(source: string): void {
  if (source) emit("toggleExpand", source);
}

function onFocusGraph(source: string): void {
  if (source) emit("focusGraph", source);
}

function onDeleteSource(source: string): void {
  if (source) emit("removeSource", source);
}

function onDeleteParagraph(hash: string): void {
  if (hash) emit("removeParagraph", hash);
}
</script>

<template>
  <div>
    <div v-if="loading && !items.length" class="empty">正在读取当前作用域来源列表…</div>
    <div v-else-if="!items.length" class="empty">
      {{
        summaryMode
          ? "当前作用域暂无记忆来源批次"
          : "未找到匹配当前节点或关系边筛选条件的记忆段落"
      }}
    </div>

    <div v-else class="rows source-rows">
      <div v-for="(item, idx) in items" :key="keyOf(item, idx)" class="source-row">
        <!-- 筛选段落行 -->
        <template v-if="isParagraph(item)">
          <div class="source-row-head">
            <h3 class="source-row-title">{{ paragraphTitle(item) }}</h3>
            <div class="source-row-actions">
              <button
                v-if="item.source"
                class="btn sm"
                type="button"
                @click="onFocusGraph(item.source)"
              >
                聚焦图谱
              </button>
              <button
                class="btn danger sm"
                type="button"
                :disabled="deleting"
                @click="onDeleteParagraph(item.hash || '')"
              >
                删除段落
              </button>
            </div>
          </div>
          <p v-if="item.content" class="para-content">{{ item.content }}</p>
          <div class="tags tabular">
            <span v-if="paragraphCreatedLabel(item)" class="tag">
              {{ paragraphCreatedLabel(item) }}
            </span>
            <span v-if="item.hash" class="tag mono">
              哈希 {{ paragraphHashShort(item.hash) }}
            </span>
          </div>
        </template>

        <!-- 来源汇总行（点击展开段落明细） -->
        <template v-else>
          <div
            class="summary-header-clickable"
            role="button"
            tabindex="0"
            :aria-expanded="isExpanded(item)"
            @click="onToggleRow(sourceNameOf(item))"
            @keydown.enter.prevent="onToggleRow(sourceNameOf(item))"
            @keydown.space.prevent="onToggleRow(sourceNameOf(item))"
          >
            <div class="summary-left">
              <h3 class="source-row-title">
                <span class="expand-arrow" aria-hidden="true">{{ isExpanded(item) ? "▾" : "▸" }}</span>
                {{ sourceNameOf(item) || "未命名来源" }}
              </h3>
              <div class="tags tabular">
                <span
                  v-for="tag in summaryTags(item as SourceSummaryItem)"
                  :key="tag"
                  class="tag"
                >
                  {{ tag }}
                </span>
              </div>
            </div>

            <div class="source-row-actions" @click.stop>
              <button
                class="btn sm"
                type="button"
                @click="onToggleRow(sourceNameOf(item))"
              >
                {{ isExpanded(item) ? "收起明细" : "展开段落" }}
              </button>
              <button
                class="btn sm"
                type="button"
                @click="onFocusGraph(sourceNameOf(item))"
              >
                聚焦图谱
              </button>
              <button
                class="btn danger sm"
                type="button"
                :disabled="deleting"
                @click="onDeleteSource(sourceNameOf(item))"
              >
                删除来源
              </button>
            </div>
          </div>

          <!-- 内联展开的段落明细列表（缩进细线行，无多余卡片框） -->
          <div v-if="isExpanded(item)" class="expanded-drawer">
            <div v-if="loadingExpanded" class="empty">正在读取该来源的段落明细…</div>
            <div v-else-if="!expandedParagraphs?.length" class="empty">
              该来源下暂无可展示的段落内容
            </div>
            <div v-else class="rows expanded-para-list">
              <div
                v-for="(para, pi) in expandedParagraphs"
                :key="para.hash || pi"
                class="expanded-para-row"
              >
                <div class="expanded-para-body">
                  <p class="para-content">{{ para.content || "（空段落）" }}</p>
                  <div class="tags tabular">
                    <span v-if="paragraphCreatedLabel(para)" class="tag">
                      {{ paragraphCreatedLabel(para) }}
                    </span>
                    <span v-if="para.hash" class="tag mono">
                      哈希 {{ paragraphHashShort(para.hash) }}
                    </span>
                  </div>
                </div>
                <button
                  class="btn danger sm"
                  type="button"
                  :disabled="deleting || !para.hash"
                  @click="onDeleteParagraph(para.hash || '')"
                >
                  删除段落
                </button>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.source-rows {
  border-top: 1px solid var(--hairline);
}

.source-row {
  padding: 12px 0;
}

.source-row-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.source-row-title {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.source-row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-header-clickable {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.summary-header-clickable:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 4px;
}

.summary-left {
  flex: 1 1 240px;
  min-width: 0;
}

.expand-arrow {
  display: inline-block;
  width: 16px;
  color: var(--accent);
}

.para-content {
  margin: 6px 0 8px;
  font-size: 13.5px;
  line-height: 1.58;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}

.expanded-drawer {
  margin-top: 12px;
  padding-left: 16px;
  border-left: 2px solid var(--hairline-strong);
}

.expanded-para-list {
  border-top: 1px solid var(--hairline);
}

.expanded-para-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  flex-wrap: wrap;
}

.expanded-para-body {
  flex: 1 1 220px;
  min-width: 0;
}
</style>
