<script setup lang="ts">
// Dashboard 组合面：总览。组织子组件 + 在挂载时加载数据。
// 修复 C5（scope 统一）：挂载先 loadScopes（写 resolvedScope），再 refreshAll
// （stats/status 统一经 effectiveScope 请求）→ 节点总量无需"载入图谱"即显示。
// 修复 C2（refreshAll 竞态）：各数据写独立 store 字段，子组件各读各的，无共享 DOM。
import { computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import MetricCards from "@/components/dashboard/MetricCards.vue";
import RuntimeBanner from "@/components/dashboard/RuntimeBanner.vue";
import RuntimeOverview from "@/components/dashboard/RuntimeOverview.vue";
import CallTrendChart from "@/components/dashboard/CallTrendChart.vue";
import ServiceList from "@/components/dashboard/ServiceList.vue";
import { useDashboardStore } from "@/stores/dashboard";
import { useScope } from "@/composables/useScope";
import { runtimeMessageText, runtimeLabel } from "@/utils/dashboardText";

const dashboard = useDashboardStore();
const { loadScopes } = useScope();
const router = useRouter();

const nodes = computed(() => dashboard.stats?.graph_store.num_nodes ?? null);
const edges = computed(() => dashboard.stats?.graph_store.num_edges ?? null);
const vectors = computed(() => dashboard.stats?.vector_store.num_vectors ?? null);

const runtimeTuple = computed(() => runtimeLabel(dashboard.runtime));
const runtimeMessage = computed(() => runtimeMessageText(dashboard.runtime, "运行时自检"));
const runtimeChipTone = computed(() => {
  const tone = runtimeTuple.value[1];
  return tone === "ok" ? "ok" : tone === "bad" ? "bad" : "warn";
});

const queryTrend = computed(() => dashboard.status?.services.query);

function navigate(view: string): void {
  void router.push({ name: view });
}

onMounted(async () => {
  await loadScopes();
  await dashboard.refreshAll();
});
</script>

<template>
  <section class="view-dashboard">
    <MetricCards :nodes="nodes" :edges="edges" :vectors="vectors" />

    <RuntimeBanner
      :message="runtimeMessage"
      :chip-label="runtimeTuple[0]"
      :chip-tone="runtimeChipTone"
      @refresh="dashboard.loadRuntime(true)"
    />

    <RuntimeOverview />

    <CallTrendChart
      :buckets="queryTrend?.trend_buckets"
      :total-count="queryTrend?.trend_total_count"
      :bucket-seconds="queryTrend?.trend_bucket_seconds"
      :seconds="queryTrend?.trend_seconds"
    />

    <ServiceList @navigate="navigate" />
  </section>
</template>

<style scoped>
.view-dashboard {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
</style>
