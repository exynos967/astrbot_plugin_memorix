<script setup lang="ts">
// Episodes 组合面：左列查询 toolbar + 列表，右列重建面板 + 详情面板。
// 从 legacy view-episodes（index.html 行 2203-2242）迁移为子组件组合。
// onMounted 预加载列表；详情/删除走 store action。
import { computed, onMounted } from "vue";
import EpisodeQueryPanel from "@/components/episodes/EpisodeQueryPanel.vue";
import EpisodeListPanel from "@/components/episodes/EpisodeListPanel.vue";
import EpisodeRebuildPanel from "@/components/episodes/EpisodeRebuildPanel.vue";
import EpisodeDetailPanel from "@/components/episodes/EpisodeDetailPanel.vue";
import { useEpisodeStore } from "@/stores/episode";

const store = useEpisodeStore();

const selectedId = computed(() => store.detail?.episode_id || "");

onMounted(() => {
  void store.loadList();
});
</script>

<template>
  <section class="view-episodes">
    <div class="grid-2">
      <div class="episodes-col">
        <EpisodeQueryPanel />
        <EpisodeListPanel :items="store.list" :selected-id="selectedId" @detail="store.loadDetail" />
      </div>
      <div class="episodes-col">
        <EpisodeRebuildPanel />
        <EpisodeDetailPanel
          :detail="store.detail"
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
  gap: 4px;
}

.episodes-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>
