<script setup lang="ts">
// Memory 组合面：记忆状态 + 关系操作 + 回收站。
// 从 legacy view-memory（index.html 行 2331-2352）迁移为子组件组合。
// 挂载时加载 status + recycle（与 legacy setView("memory") 触发的 loadMemoryStatus+loadRecycle 一致）。
import { onMounted } from "vue";
import MemoryStatusPanel from "@/components/memory/MemoryStatusPanel.vue";
import MemoryActionsPanel from "@/components/memory/MemoryActionsPanel.vue";
import RecyclePanel from "@/components/memory/RecyclePanel.vue";
import DeleteOperationsPanel from "@/components/memory/DeleteOperationsPanel.vue";
import { useMemoryStore } from "@/stores/memory";
import type { MemoryAction } from "@/services/memoryApi";

const memory = useMemoryStore();

async function onRun(action: MemoryAction, id: string): Promise<void> {
  await memory.runAction(action, id);
}

async function onRestore(hash: string, type: string): Promise<void> {
  await memory.restore(hash, type);
}

onMounted(() => {
  void Promise.allSettled([memory.loadStatus(), memory.loadRecycle()]);
});
</script>

<template>
  <section class="view-memory">
    <div class="grid-2">
      <MemoryStatusPanel :status="memory.status" />
      <MemoryActionsPanel
        :result="memory.lastAction"
        :result-id="memory.lastActionId"
        :result-action="memory.lastActionName"
        :busy="memory.actionBusy"
        @run="onRun"
      />
    </div>
    <div class="toolbar" style="margin-top: 12px">
      <button class="btn" :disabled="memory.loadingStatus" @click="memory.loadStatus()">刷新状态</button>
      <button class="btn" :disabled="memory.loadingRecycle" @click="memory.loadRecycle()">刷新回收站</button>
    </div>
    <RecyclePanel :items="memory.recycle" :busy="memory.actionBusy" @restore="onRestore" />
    <DeleteOperationsPanel />
  </section>
</template>

<style scoped>
.view-memory {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
</style>
