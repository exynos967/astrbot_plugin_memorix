<script setup lang="ts">
// Episode 详情面板：扁平 1px 细线布局（移除 4 层盒套盒），直接渲染情景摘要、参与人物、关键词与证据段落行。
import type { Episode } from "@/services/episodeApi";
import {
  episodeGranularityLabel,
  episodeKeywords,
  episodeParagraphCountLabel,
  episodeParticipants,
  episodeSourceLabel,
  episodeSummary,
  episodeTimeRange,
  episodeTitle,
  paragraphTypeLabel,
} from "@/utils/episodeText";
import { formatTs } from "@/utils/time";

defineProps<{
  detail: Episode | null;
  loading?: boolean;
  deleting: boolean;
}>();

const emit = defineEmits<{
  "delete-paragraph": [hash: string];
}>();

function hashShort(hash: string | undefined): string {
  return hash ? hash.slice(0, 12) : "";
}

function onDelete(hash: string | undefined): void {
  if (hash) emit("delete-paragraph", hash);
}

function paragraphTime(p: { created_at?: number; [k: string]: unknown }): string {
  return p.created_at != null ? formatTs(p.created_at) : "";
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>情景记忆详情</h2>
      <span v-if="detail?.episode_id" class="tag mono">
        ID {{ detail.episode_id.slice(0, 12) }}
      </span>
    </div>

    <div v-if="loading" class="detail-skeleton" aria-busy="true">
      <div class="sk-line" style="width: 60%; height: 18px" />
      <div class="sk-line" style="width: 95%; height: 64px" />
      <div class="sk-line" style="width: 75%; height: 42px" />
    </div>

    <div v-else-if="!detail" class="empty">点击左侧情景条目查看完整事件摘要与支撑段落</div>

    <div v-else class="detail-content">
      <h3 class="detail-heading">{{ episodeTitle(detail) }}</h3>

      <div class="tags tabular">
        <span class="tag">来源：{{ episodeSourceLabel(detail) }}</span>
        <span class="tag">{{ episodeTimeRange(detail) }}</span>
        <span v-if="episodeGranularityLabel(detail.time_granularity)" class="tag">
          粒度：{{ episodeGranularityLabel(detail.time_granularity) }}
        </span>
        <span class="tag">{{ episodeParagraphCountLabel(detail) }}</span>
        <span
          v-if="detail.llm_confidence != null && Number.isFinite(Number(detail.llm_confidence))"
          class="tag ok"
        >
          置信度 {{ Number(detail.llm_confidence).toFixed(2) }}
        </span>
      </div>

      <div class="summary-section">
        <span class="callout-label">情景摘要</span>
        <p class="summary-text">
          {{ episodeSummary(detail) || "该情景暂无摘要文本" }}
        </p>
      </div>

      <div v-if="episodeParticipants(detail).length" class="sub-section">
        <h4>参与人物</h4>
        <div class="tags">
          <span v-for="p in episodeParticipants(detail)" :key="p" class="tag ok">{{ p }}</span>
        </div>
      </div>

      <div v-if="episodeKeywords(detail).length" class="sub-section">
        <h4>事件关键词</h4>
        <div class="tags">
          <span v-for="kw in episodeKeywords(detail)" :key="kw" class="tag mono">{{ kw }}</span>
        </div>
      </div>

      <div class="sub-section">
        <h4 class="tabular">关联证据段落 ({{ detail.paragraphs?.length ?? 0 }})</h4>
        <div v-if="!detail.paragraphs?.length" class="empty">暂无关联原文段落</div>
        <div v-else class="rows paragraph-list">
          <div v-for="(p, idx) in detail.paragraphs" :key="p.hash || idx" class="paragraph-row">
            <div class="paragraph-body">
              <p v-if="p.content">{{ p.content }}</p>
              <div class="tags tabular">
                <span v-if="p.type" class="tag">{{ paragraphTypeLabel(p.type) }}</span>
                <span v-if="p.source" class="tag">来源：{{ p.source }}</span>
                <span v-if="paragraphTime(p)" class="tag">{{ paragraphTime(p) }}</span>
                <span v-if="p.hash" class="tag mono">哈希 {{ hashShort(p.hash) }}</span>
              </div>
            </div>
            <button
              class="btn danger sm"
              type="button"
              :disabled="deleting || !p.hash"
              @click="onDelete(p.hash)"
            >
              删除段落
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.detail-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.detail-heading {
  margin: 0;
  font-size: 16px;
  font-weight: 680;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.summary-section {
  padding: 12px 14px;
  border-left: 3px solid var(--accent);
  background: var(--surface-soft);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

.callout-label {
  display: block;
  font-size: 12px;
  font-weight: 650;
  color: var(--accent);
  margin-bottom: 4px;
}

.summary-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.62;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ink);
}

.sub-section {
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.sub-section h4 {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 650;
  color: var(--muted);
}

.paragraph-list {
  border-top: 1px solid var(--hairline);
}

.paragraph-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  flex-wrap: wrap;
}

.paragraph-body {
  flex: 1 1 240px;
  min-width: 0;
}

.paragraph-body p {
  margin: 0 0 8px;
  color: var(--text);
  font-size: 13.5px;
  line-height: 1.58;
  white-space: pre-wrap;
  word-break: break-word;
}

.detail-skeleton {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sk-line {
  border-radius: var(--radius-sm);
  background: var(--hairline);
  animation: sk-pulse 1.4s ease-in-out infinite;
}

@keyframes sk-pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 0.9;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sk-line {
    animation: none;
  }
}
</style>
