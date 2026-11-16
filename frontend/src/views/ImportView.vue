<script setup lang="ts">
// Import 组合面：左栏「导入任务创建 + 结构化任务监控与分块管理」，右栏「会话摘要任务」。
import { onMounted, onBeforeUnmount } from "vue";
import { useTaskStore } from "@/stores/task";
import ImportTaskPanel from "@/components/import/ImportTaskPanel.vue";
import TaskStatusPanel from "@/components/import/TaskStatusPanel.vue";
import SummaryTaskPanel from "@/components/import/SummaryTaskPanel.vue";

const store = useTaskStore();

onMounted(() => {
  store.setViewActive(true);
});

onBeforeUnmount(() => {
  store.setViewActive(false);
});
</script>

<template>
  <section class="view-import grid-2">
    <div class="import-col">
      <ImportTaskPanel />
      <TaskStatusPanel />
    </div>
    <div class="import-col">
      <SummaryTaskPanel />
    </div>
  </section>
</template>

<style scoped>
.view-import {
  align-items: start;
  gap: 14px;
}

.import-col {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.view-import :deep(.band) {
  margin-top: 0;
}
</style>
