<script setup lang="ts">
// 记忆状态面板：KPI（4 块）+ 配置展示（8 项）。
// 从 legacy renderMemoryStatus（index.html 行 4563-4605）迁移。
// 纯展示（props down），派生由 utils/memoryText 完成。
import { computed } from "vue";
import type { MemoryStatus } from "@/services/memoryApi";
import { deriveMemoryConfigs, deriveMemoryKpis } from "@/utils/memoryText";

const props = defineProps<{ status: MemoryStatus | null }>();

const kpis = computed(() => deriveMemoryKpis(props.status));
const configs = computed(() => deriveMemoryConfigs(props.status));
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title"><h2>记忆状态</h2></div>
    <div v-if="!status" class="memory-dashboard"><div class="empty">等待加载</div></div>
    <div v-else class="memory-dashboard">
      <div class="memory-kpis">
        <div v-for="kpi in kpis" :key="kpi.label" class="memory-kpi">
          <span>{{ kpi.label }}</span>
          <strong>{{ kpi.value }}</strong>
          <span>{{ kpi.note }}</span>
        </div>
      </div>
      <div class="memory-config">
        <div v-for="item in configs" :key="item.label" class="memory-config-item">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </div>
    </div>
  </div>
</template>
