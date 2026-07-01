<script setup lang="ts">
// Episode 详情展示：标题/来源/时间区间/参与者/keywords/summary/段落列表（含删除）。
// 从 legacy loadEpisodeDetail（index.html 行 4365-4369）迁移为纯展示（省略原始 JSON 抽屉，P8 接入）。
// 段落删除按钮上抛 delete-paragraph(hash)；空态提示选择一条 episode。
import type { Episode } from "@/services/episodeApi";
import {
  episodeKeywords,
  episodeParticipants,
  episodeTimeRange,
  episodeTitle,
} from "@/utils/episodeText";
import { formatTs } from "@/utils/time";

defineProps<{ detail: Episode | null; deleting: boolean }>();

const emit = defineEmits<{
  "delete-paragraph": [hash: string];
}>();

/** 段落 hash 短显示（前 8 位）。 */
function hashShort(hash: string | undefined): string {
  return hash ? hash.slice(0, 8) : "";
}

function onDelete(hash: string | undefined): void {
  if (hash) emit("delete-paragraph", hash);
}

/** 段落时间标签（created_at 优先）。 */
function paragraphTime(p: { created_at?: number; [k: string]: unknown }): string {
  return p.created_at != null ? formatTs(p.created_at) : "";
}
</script>

<template>
  <div class="band">
    <div class="panel-title"><h2>Episode 详情</h2></div>
    <div class="result-list">
      <div v-if="!detail" class="empty">选择一条 episode 查看详情</div>
      <div v-else class="result">
        <div class="result-head">
          <h3>{{ episodeTitle(detail) }}</h3>
        </div>
        <div class="tags">
          <span class="tag">{{ detail.source || "source" }}</span>
          <span class="tag">{{ episodeTimeRange(detail) }}</span>
        </div>
        <div v-if="episodeParticipants(detail).length" class="sub-section">
          <h4>参与者</h4>
          <div class="tags">
            <span v-for="p in episodeParticipants(detail)" :key="p" class="tag">{{ p }}</span>
          </div>
        </div>
        <div v-if="episodeKeywords(detail).length" class="sub-section">
          <h4>关键词</h4>
          <div class="tags">
            <span v-for="kw in episodeKeywords(detail)" :key="kw" class="tag mono">{{ kw }}</span>
          </div>
        </div>
        <div v-if="detail.paragraphs && detail.paragraphs.length" class="sub-section">
          <h4>段落</h4>
          <div class="paragraph-list">
            <div v-for="(p, idx) in detail.paragraphs" :key="p.hash || idx" class="paragraph-row">
              <div class="paragraph-body">
                <p v-if="p.content">{{ p.content }}</p>
                <div class="tags">
                  <span v-if="p.hash" class="tag mono">{{ hashShort(p.hash) }}</span>
                  <span v-if="paragraphTime(p)" class="tag">{{ paragraphTime(p) }}</span>
                  <span v-if="p.type" class="tag">{{ p.type }}</span>
                </div>
              </div>
              <div class="result-actions" style="margin-top: 0">
                <button
                  class="btn danger"
                  :disabled="deleting"
                  @click="onDelete(p.hash)"
                >删除</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sub-section {
  margin-top: 12px;
}

.sub-section h4 {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--muted);
}

.summary-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
}

.paragraph-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.paragraph-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
}

.paragraph-body {
  flex: 1;
  min-width: 0;
}

.paragraph-body p {
  margin: 0 0 6px;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
