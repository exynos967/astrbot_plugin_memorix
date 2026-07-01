<script setup lang="ts">
// Import 组合面：左列导入任务 + 任务状态，右列摘要任务。
// 从 legacy view-import（index.html 行 2244-2279）迁移为子组件组合。
// 修复 legacy 缺陷：legacy 无任务轮询且无定时器清理。store 已内建轮询，
// 本 view 仅在卸载时调 store.stopPolling() 清理定时器，杜绝泄漏。
// 挂载时不自动启动任务（无遗留任务可恢复）。
import { onMounted, onBeforeUnmount } from "vue";
import { useTaskStore } from "@/stores/task";
import ImportTaskPanel from "@/components/import/ImportTaskPanel.vue";
import TaskStatusPanel from "@/components/import/TaskStatusPanel.vue";
import SummaryTaskPanel from "@/components/import/SummaryTaskPanel.vue";

const store = useTaskStore();

onMounted(() => {
  // 标记视图活跃，允许 createImport/createSummary 在 in-flight 完成后启动轮询。
  store.setViewActive(true);
});

onBeforeUnmount(() => {
  // 标记离开 + 清理定时器，杜绝创建 in-flight 完成后泄漏轮询。
  store.setViewActive(false);
});
</script>

<template>
  <section class="view-import grid-2">
    <div>
      <ImportTaskPanel />
      <TaskStatusPanel />
    </div>
    <div>
      <SummaryTaskPanel />
    </div>
  </section>
</template>

<style scoped>
.view-import {
  align-items: start;
}

.view-import > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
</style>
