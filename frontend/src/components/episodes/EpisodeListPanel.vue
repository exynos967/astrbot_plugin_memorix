<script setup lang="ts">
// Episode 列表：精简为标题行列表，点击载入右侧详情。
// 从 legacy renderGenericResults（index.html 行 4258-4286）迁移简化。
import type { Episode } from "@/services/episodeApi";

defineProps<{ items: Episode[]; selectedId?: string }>();

const emit = defineEmits<{
  detail: [episodeId: string];
}>();

function onSelect(ep: Episode): void {
  if (ep.episode_id) emit("detail", ep.episode_id);
}

/** 可展示标题：title 优先、回退 episode_id。 */
function titleOf(ep: Episode): string {
  return ep.title || ep.episode_id || "episode";
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>Episode 列表</h2>
      <span class="section-label">{{ items.length }} 条</span>
    </div>
    <div class="result-list">
      <div v-if="!items.length" class="empty">暂无 episode</div>
      <button
        v-for="ep in items"
        :key="ep.episode_id"
        class="episode-row"
        :class="{ active: ep.episode_id === selectedId }"
        @click="onSelect(ep)"
      >
        {{ titleOf(ep) }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.episode-row {
  display: block;
  width: 100%;
  padding: 11px 14px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.56);
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.episode-row:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.episode-row.active {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-strong);
}
</style>
