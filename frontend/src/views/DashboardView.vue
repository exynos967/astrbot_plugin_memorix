<script setup lang="ts">
// Dashboard 组合面：总览。指标卡 + 调用趋势。
// 修复 C5（scope 统一）：挂载先 loadScopes（写 resolvedScope），再 refreshAll
// （stats/status 统一经 effectiveScope 请求）→ 节点总量无需"载入图谱"即显示。
// 修复 C2（refreshAll 竞态）：各数据写独立 store 字段，子组件各读各的，无共享 DOM。
import { computed, onMounted } from "vue";
import MetricCards from "@/components/dashboard/MetricCards.vue";
import CallTrendChart from "@/components/dashboard/CallTrendChart.vue";
import { useDashboardStore } from "@/stores/dashboard";
import { useScope } from "@/composables/useScope";

const dashboard = useDashboardStore();
const { loadScopes } = useScope();

const nodes = computed(() => dashboard.stats?.graph_store.num_nodes ?? null);
const edges = computed(() => dashboard.stats?.graph_store.num_edges ?? null);
const vectors = computed(() => dashboard.stats?.vector_store.num_vectors ?? null);

const queryTrend = computed(() => dashboard.status?.services.query);

onMounted(async () => {
  await loadScopes();
  await dashboard.refreshAll();
});
</script>

<template>
  <section class="view-dashboard">
    <MetricCards :nodes="nodes" :edges="edges" :vectors="vectors" />

    <CallTrendChart
      class="trend-chart-fill"
      :buckets="queryTrend?.trend_buckets"
      :total-count="queryTrend?.trend_total_count"
      :bucket-seconds="queryTrend?.trend_bucket_seconds"
      :seconds="queryTrend?.trend_seconds"
    />
  </section>
</template>

<style scoped>
.view-dashboard {
  display: flex;
  flex-direction: column;
  gap: 4px;
  /* 填满 view-stack，让内部 flex:1 子项（趋势图）拿到确定的高度 */
  flex: 1;
}

/* 调用趋势图撑满剩余高度 */
.trend-chart-fill {
  flex: 1;
  min-height: 0;
}
</style>
