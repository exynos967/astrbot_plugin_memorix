<script setup lang="ts">
// 来源/段落列表渲染。
// 从 legacy renderSources（index.html 行 4502-4516）迁移。
// 两种行型：
//   - summary 模式（无 node/edge）：标题=sourceNameOf，tags=[count, updated]，删除 source → emit remove-source(source)
//   - paragraph 模式（有 hash）：标题=source||hash，正文 content，tag=hash short，删除 → emit remove-paragraph(hash)
// legacy 的"聚焦图谱"按钮依赖 graph view（P8），本阶段省略，由 P8 接入。
import type { SourceListItem, SourceParagraphItem, SourceSummaryItem } from "@/services/sourceApi";
import {
  isParagraph,
  paragraphHashShort,
  sourceCountLabel,
  sourceNameOf,
  sourceUpdatedLabel,
} from "@/utils/sourceText";

defineProps<{ items: SourceListItem[]; deleting?: boolean }>();

const emit = defineEmits<{
  removeSource: [source: string];
  removeParagraph: [hash: string];
}>();

/** summary 行展示用的标签。 */
function summaryTags(item: SourceSummaryItem): string[] {
  return [sourceCountLabel(item), sourceUpdatedLabel(item)].filter(Boolean);
}

/** 行 key：paragraph 用 hash，summary 用 source 名，再回退下标。 */
function keyOf(item: SourceListItem, idx: number): string {
  if (isParagraph(item)) return item.hash || `p${idx}`;
  return sourceNameOf(item) || `s${idx}`;
}

/** 段落行标题：优先 source 名，否则 hash。 */
function paragraphTitle(item: SourceParagraphItem): string {
  return item.source || item.hash || "source";
}

function onDeleteSource(source: string): void {
  if (source) emit("removeSource", source);
}

function onDeleteParagraph(hash: string): void {
  if (hash) emit("removeParagraph", hash);
}
</script>

<template>
  <div class="result-list">
    <div v-if="!items.length" class="empty">没有来源段落</div>
    <template v-else>
      <div v-for="(item, idx) in items" :key="keyOf(item, idx)" class="result">
        <!-- paragraph 行 -->
        <template v-if="isParagraph(item)">
          <div class="result-head">
            <h3>{{ paragraphTitle(item) }}</h3>
            <div class="result-actions" style="margin-top: 0">
              <button class="btn danger" :disabled="deleting" @click="onDeleteParagraph(item.hash || '')">删除</button>
            </div>
          </div>
          <p v-if="item.content">{{ item.content }}</p>
          <div class="tags">
            <span v-if="item.hash" class="tag mono">{{ paragraphHashShort(item.hash) }}</span>
          </div>
        </template>
        <!-- summary 行 -->
        <template v-else>
          <div class="result-head">
            <h3>{{ sourceNameOf(item) || "source" }}</h3>
            <div class="result-actions" style="margin-top: 0">
              <button class="btn danger" :disabled="deleting" @click="onDeleteSource(sourceNameOf(item))">删除 source</button>
            </div>
          </div>
          <div class="tags">
            <span v-for="tag in summaryTags(item as SourceSummaryItem)" :key="tag" class="tag">{{ tag }}</span>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>
