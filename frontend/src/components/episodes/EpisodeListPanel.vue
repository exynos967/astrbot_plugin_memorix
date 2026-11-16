<script setup lang="ts">
// Episode 列表面板：扁平 1px 细线行列表展示情景标题、摘要预览与元数据徽标，遵循 prefers-reduced-motion。
import type { Episode } from "@/services/episodeApi";
import {
  episodeParagraphCountLabel,
  episodeSourceLabel,
  episodeSummaryPreview,
  episodeTimeRange,
  episodeTitle,
} from "@/utils/episodeText";

defineProps<{
  items: Episode[];
  selectedId?: string;
  loading?: boolean;
  meta?: string;
}>();

const emit = defineEmits<{
  detail: [episodeId: string];
}>();

function onSelect(ep: Episode): void {
  if (ep.episode_id) emit("detail", ep.episode_id);
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>情景记忆列表</h2>
      <span class="section-label tabular">{{ meta || `共 ${items.length} 条情景` }}</span>
    </div>

    <div v-if="loading && !items.length" class="skeleton-list" aria-busy="true">
      <div v-for="n in 4" :key="n" class="skeleton-card">
        <div class="sk-line sk-title" />
        <div class="sk-line sk-body" />
        <div class="sk-line sk-tags" />
      </div>
    </div>

    <div v-else-if="!items.length" class="empty">当前筛选条件下暂无情景记忆</div>

    <div v-else class="episode-list">
      <button
        v-for="ep in items"
        :key="ep.episode_id"
        type="button"
        class="episode-row"
        :class="{ active: ep.episode_id === selectedId }"
        @click="onSelect(ep)"
      >
        <div class="ep-row-head">
          <strong class="ep-title">{{ episodeTitle(ep) }}</strong>
          <span v-if="ep.score != null" class="tag mono tabular">
            相关度 {{ Number(ep.score).toFixed(3) }}
          </span>
        </div>

        <p v-if="episodeSummaryPreview(ep)" class="ep-preview">
          {{ episodeSummaryPreview(ep, 120) }}
        </p>

        <div class="tags tabular">
          <span class="tag">{{ episodeSourceLabel(ep) }}</span>
          <span class="tag">{{ episodeParagraphCountLabel(ep) }}</span>
          <span class="tag">{{ episodeTimeRange(ep) }}</span>
        </div>
      </button>
    </div>
  </div>
</template>

<style scoped>
.episode-list {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--hairline);
}

.episode-row {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  width: 100%;
  padding: 12px 10px;
  border: 0;
  border-bottom: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  transition: background var(--m-out) var(--ease);
}

.episode-row:hover {
  background: var(--nav-soft);
}

.episode-row.active {
  background: var(--accent-soft);
}

.episode-row:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.ep-row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ep-title {
  font-size: 14px;
  font-weight: 650;
  color: var(--ink);
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.episode-row.active .ep-title {
  color: var(--accent-ink);
}

.ep-preview {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.52;
  color: var(--muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.skeleton-list {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--hairline);
}

.skeleton-card {
  padding: 12px 0;
  border-bottom: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sk-line {
  height: 12px;
  border-radius: var(--radius-sm);
  background: var(--hairline);
  animation: sk-pulse 1.4s ease-in-out infinite;
}

.sk-title {
  width: 55%;
  height: 15px;
}
.sk-body {
  width: 90%;
}
.sk-tags {
  width: 42%;
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
