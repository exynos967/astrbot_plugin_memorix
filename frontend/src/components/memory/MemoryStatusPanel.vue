<script setup lang="ts">
// 记忆状态面板：顶部 4 项关键指标 + 扁平 1px 细线衰减参数表（消除 8 个嵌套小卡片盒套盒）。
import { computed } from "vue";
import type { MemoryStatus } from "@/services/memoryApi";
import { deriveMemoryConfigs, deriveMemoryKpis } from "@/utils/memoryText";

const props = defineProps<{
  status: MemoryStatus | null;
  loading?: boolean;
}>();

const emit = defineEmits<{ refresh: [] }>();

const kpis = computed(() => deriveMemoryKpis(props.status));
const configs = computed(() => deriveMemoryConfigs(props.status));
</script>

<template>
  <div class="band status-panel">
    <div class="panel-title">
      <h2>记忆衰减与保护状态</h2>
      <button class="btn sm" type="button" :disabled="loading" @click="emit('refresh')">
        {{ loading ? "刷新中…" : "刷新状态" }}
      </button>
    </div>

    <div v-if="loading && !status" class="empty">正在读取当前作用域记忆统计…</div>
    <div v-else-if="!status" class="empty">暂无记忆状态数据</div>
    <div v-else class="memory-dashboard">
      <div class="memory-kpis">
        <div v-for="kpi in kpis" :key="kpi.label" class="memory-kpi">
          <span>{{ kpi.label }}</span>
          <strong class="tabular">{{ kpi.value }}</strong>
          <small class="kpi-note">{{ kpi.note }}</small>
        </div>
      </div>

      <dl class="spec-grid">
        <div v-for="item in configs" :key="item.label" class="spec-row">
          <dt>{{ item.label }}</dt>
          <dd class="tabular">{{ item.value }}</dd>
        </div>
      </dl>
    </div>
  </div>
</template>

<style scoped>
.status-panel {
  margin-top: 0;
}

.kpi-note {
  display: block;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--muted);
}

.spec-grid {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 20px;
  border-top: 1px solid var(--hairline);
}

.spec-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid var(--hairline);
}

.spec-row dt {
  font-size: 12.5px;
  color: var(--muted);
}

.spec-row dd {
  margin: 0;
  font-size: 13px;
  font-weight: 650;
  color: var(--ink);
}

@media (max-width: 640px) {
  .spec-grid {
    grid-template-columns: 1fr;
  }
}
</style>
