<script setup lang="ts">
import { ref, watch } from "vue";
import { memoryAdmin, type DeleteOperation, type DeleteOperations } from "@/services/memoryAdminApi";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { errText } from "@/utils/error";

const graph = useGraphStore();
const app = useAppStore();
const items = ref<DeleteOperation[]>([]);
const busy = ref(false);
const message = ref("");
let revision = 0;
async function reload() {
  const seq = ++revision;
  try {
    const result = await memoryAdmin<DeleteOperations>("memory/delete-admin", "list_operations", { limit: 50 }, graph.effectiveScope());
    if (seq === revision) items.value = result.items;
  } catch (error) { if (seq === revision) app.pushError(errText(error), "deleteOperations"); }
}
async function restore(operationId: string) {
  if (busy.value) return;
  busy.value = true;
  const scope = graph.effectiveScope();
  try {
    const result = await memoryAdmin<{ success: boolean; restored: number; skipped?: string[]; projection?: { pending: number } }>(
      "memory/delete-admin", "restore", { operation_id: operationId }, scope,
    );
    if (scope !== graph.effectiveScope()) return;
    message.value = `已恢复 ${result.restored} 项，跳过 ${result.skipped?.length || 0} 项，待同步索引 ${result.projection?.pending || 0} 项。`;
    await reload();
  } catch (error) { app.pushError(errText(error), "restoreDeleteOperation"); }
  finally { busy.value = false; }
}
watch(() => graph.effectiveScope(), () => { items.value = []; message.value = ""; void reload(); }, { immediate: true });
</script>

<template>
  <div class="band">
    <div class="panel-title"><h2>删除操作记录</h2><button class="btn" :disabled="busy" @click="reload">刷新</button></div>
    <p>按整次操作恢复段落、实体及其关联。已经重新写入或被其他操作删除的内容会保留当前状态。</p>
    <p v-if="message" role="status">{{ message }}</p>
    <article v-for="item in items" :key="item.operation_id" class="operation">
      <div>{{ new Date(item.created_at * 1000).toLocaleString() }} · {{ item.mode }} · {{ item.status }}</div>
      <small>{{ item.operation_id }}</small>
      <p v-if="item.summary?.counts">段落 {{ item.summary.counts.paragraph || 0 }} · 实体 {{ item.summary.counts.entity || 0 }} · 关系 {{ item.summary.counts.relation || 0 }}</p>
      <button v-if="item.status === 'executed'" class="btn" :disabled="busy" @click="restore(item.operation_id)">恢复此次删除</button>
    </article>
    <p v-if="!items.length">暂无删除记录。</p>
  </div>
</template>

<style scoped>.operation { padding: 12px 0; border-bottom: 1px solid var(--border-color, #8884); } small { overflow-wrap: anywhere; }</style>
