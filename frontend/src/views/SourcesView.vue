<script setup lang="ts">
// Sources 组合面：查询 toolbar + 来源/段落列表。
// 从 legacy view-sources（index.html 行 2315-2329）迁移为子组件组合。
// 挂载时调 store.load()（默认 summary 模式，三参全空）。
import { onMounted } from "vue";
import SourceQueryPanel from "@/components/sources/SourceQueryPanel.vue";
import SourceListPanel from "@/components/sources/SourceListPanel.vue";
import { useSourcesStore } from "@/stores/sources";

const store = useSourcesStore();

onMounted(() => {
  void store.load();
});
</script>

<template>
  <section class="view-sources">
    <SourceQueryPanel />
    <div class="band">
      <div class="panel-title">
        <h2>来源与段落</h2>
        <span class="section-label">{{ store.meta || "-" }}</span>
      </div>
      <SourceListPanel
        :items="store.items"
        @remove-source="store.removeSource"
        @remove-paragraph="store.removeParagraph"
      />
    </div>
  </section>
</template>

<style scoped>
.view-sources {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
</style>
