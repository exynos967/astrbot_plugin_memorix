<script setup lang="ts">
// Memory 综合治理视图：记忆衰减状态 + 关系保护/置顶 + 双池向量维护 + 结构化事实台账 + 回收站 + 可恢复删除审计。
import { onMounted } from "vue";
import MemoryStatusPanel from "@/components/memory/MemoryStatusPanel.vue";
import MemoryActionsPanel from "@/components/memory/MemoryActionsPanel.vue";
import VectorMaintenancePanel from "@/components/memory/VectorMaintenancePanel.vue";
import FactLedgerPanel from "@/components/memory/FactLedgerPanel.vue";
import RecyclePanel from "@/components/memory/RecyclePanel.vue";
import DeleteOperationsPanel from "@/components/memory/DeleteOperationsPanel.vue";
import { useMemoryStore } from "@/stores/memory";
import { useScope } from "@/composables/useScope";
import type { MemoryAction } from "@/services/memoryApi";

const memory = useMemoryStore();
const { loadScopes } = useScope();

async function onRun(action: MemoryAction, id: string, hours = 24): Promise<void> {
  await memory.runAction(action, id, hours);
}

async function onRestore(hash: string, type: string): Promise<void> {
  await memory.restore(hash, type);
}

onMounted(async () => {
  await loadScopes();
  await Promise.allSettled([
    memory.loadStatus(),
    memory.loadRecycle(),
    memory.loadVectorStatus(),
  ]);
});
</script>

<template>
  <section class="view-memory">
    <div class="grid-2">
      <MemoryStatusPanel
        :status="memory.status"
        :loading="memory.loadingStatus"
        @refresh="memory.loadStatus()"
      />
      <MemoryActionsPanel
        :result="memory.lastAction"
        :result-id="memory.lastActionId"
        :result-action="memory.lastActionName"
        :busy="memory.actionBusy"
        @run="onRun"
      />
    </div>

    <VectorMaintenancePanel />

    <FactLedgerPanel />

    <RecyclePanel
      :items="memory.recycle"
      :busy="memory.actionBusy"
      :loading="memory.loadingRecycle"
      @restore="onRestore"
      @refresh="memory.loadRecycle()"
    />

    <DeleteOperationsPanel />
  </section>
</template>

<style scoped>
.view-memory {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.view-memory > :deep(.band) {
  margin-top: 0;
}
</style>
