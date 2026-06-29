<script setup lang="ts">
// Episode 列表渲染：每项标题 + 摘要 + tags(source label, paragraph count, keywords 前5) + 详情按钮。
// 从 legacy renderGenericResults（index.html 行 4258-4286）迁移为纯展示。
// 派生均走 utils/episodeText；详情按钮上抛 detail(episode_id)。
import type { Episode } from "@/services/episodeApi";
import {
  episodeKeywords,
  episodeParagraphCountLabel,
  episodeSourceLabel,
  episodeSummary,
  episodeTitle,
} from "@/utils/episodeText";

defineProps<{ items: Episode[] }>();

const emit = defineEmits<{
  detail: [episodeId: string];
}>();

function onDetail(ep: Episode): void {
  if (ep.episode_id) emit("detail", ep.episode_id);
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>Episode 列表</h2>
      <div class="toolbar">
        <span class="section-label">{{ items.length }} 条</span>
      </div>
    </div>
    <div class="result-list">
      <div v-if="!items.length" class="empty">暂无 episode</div>
      <div v-for="ep in items" :key="ep.episode_id" class="result">
        <div>
          <div class="result-head">
            <h3>{{ episodeTitle(ep) }}</h3>
          </div>
          <p v-if="episodeSummary(ep)">{{ episodeSummary(ep) }}</p>
          <div class="tags">
            <span class="tag">{{ episodeSourceLabel(ep) }}</span>
            <span class="tag">{{ episodeParagraphCountLabel(ep) }}</span>
            <span v-for="kw in episodeKeywords(ep)" :key="kw" class="tag mono">{{ kw }}</span>
          </div>
        </div>
        <button class="btn" @click="onDetail(ep)">详情</button>
      </div>
    </div>
  </div>
</template>
