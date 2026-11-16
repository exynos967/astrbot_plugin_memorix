<script setup lang="ts">
// Episodes 视图：左侧多维筛选 + 情景摘要列表，右侧租约安全重建面板 + 扁平化情景详情。
import { computed, onMounted } from "vue";
import EpisodeQueryPanel from "@/components/episodes/EpisodeQueryPanel.vue";
import EpisodeListPanel from "@/components/episodes/EpisodeListPanel.vue";
import EpisodeRebuildPanel from "@/components/episodes/EpisodeRebuildPanel.vue";
import EpisodeDetailPanel from "@/components/episodes/EpisodeDetailPanel.vue";
import { useEpisodeStore } from "@/stores/episode";
import { useScope } from "@/composables/useScope";

const store = useEpisodeStore();
const { loadScopes } = useScope();

const selectedId = computed(() => store.detail?.episode_id || "");

onMounted(async () => {
  await loadScopes();
  await Promise.allSettled([store.loadList(), store.loadLeaseStatus()]);
});
</script>

<template>
  <section class="view-episodes">
    <div class="grid-2 episodes-grid">
      <div class="episodes-col">
        <EpisodeQueryPanel />
        <EpisodeListPanel
          :items="store.list"
          :meta="store.meta"
          :loading="store.loading"
          :selected-id="selectedId"
          @detail="store.loadDetail"
        />
      </div>
      <div class="episodes-col">
        <EpisodeRebuildPanel />
        <EpisodeDetailPanel
          :detail="store.detail"
          :loading="store.loadingDetail"
          :deleting="store.deleting"
          @delete-paragraph="store.removeParagraph"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.view-episodes {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

@media (max-width: 960px) {
  .episodes-grid {
    grid-template-columns: 1fr;
  }
}

.episodes-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.episodes-col > :deep(.band) {
  margin-top: 0;
}
</style>
