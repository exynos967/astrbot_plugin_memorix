<script setup lang="ts">
// Sources 视图：支持按节点/边筛选段落、汇总模式下点击来源行内联展开段落明细、一键在图谱中聚焦该来源以及作用域安全删除。
import { computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import SourceQueryPanel from "@/components/sources/SourceQueryPanel.vue";
import SourceListPanel from "@/components/sources/SourceListPanel.vue";
import { useSourcesStore } from "@/stores/sources";
import { useGraphStore } from "@/stores/graph";
import { useScope } from "@/composables/useScope";
import { isSummaryMode } from "@/utils/sourceText";

const router = useRouter();
const store = useSourcesStore();
const graph = useGraphStore();
const { loadScopes } = useScope();

const summaryMode = computed(() => isSummaryMode(store.lastResult));

function focusInGraph(sourceName: string): void {
  if (!sourceName) return;
  graph.sourceFocus = sourceName;
  void router.push("/graph");
}

onMounted(async () => {
  await loadScopes();
  void store.load();
  if (!graph.nodeLabels.length && !graph.loading) {
    void graph.loadGraph();
  }
});
</script>

<template>
  <section class="view-sources">
    <SourceQueryPanel />

    <div class="band">
      <div class="panel-title">
        <div>
          <h2>记忆来源与段落明细</h2>
          <span class="section-label">
            {{
              summaryMode
                ? "点击来源行展开原文段落，或跳转至知识图谱聚焦该来源子图"
                : "符合当前实体节点或关系边条件的原始记忆段落"
            }}
          </span>
        </div>
        <span class="tag tabular">{{ store.meta || "等待加载" }}</span>
      </div>

      <SourceListPanel
        :items="store.items"
        :loading="store.loading"
        :deleting="store.deleting"
        :summary-mode="summaryMode"
        :expanded-source="store.expandedSource"
        :expanded-paragraphs="store.expandedParagraphs"
        :loading-expanded="store.loadingExpanded"
        @toggle-expand="store.toggleExpandSource"
        @focus-graph="focusInGraph"
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
  gap: 16px;
}

.view-sources > :deep(.band) {
  margin-top: 0;
}
</style>
