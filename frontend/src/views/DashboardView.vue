<script setup lang="ts">
// Dashboard 总览视图：4 核心指标卡 + 快捷工作台入口 + 运行概况 + 服务分区列表 + 调用趋势图。
import { computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import MetricCards from "@/components/dashboard/MetricCards.vue";
import RuntimeBanner from "@/components/dashboard/RuntimeBanner.vue";
import RuntimeOverview from "@/components/dashboard/RuntimeOverview.vue";
import ServiceList from "@/components/dashboard/ServiceList.vue";
import CallTrendChart from "@/components/dashboard/CallTrendChart.vue";
import { useDashboardStore } from "@/stores/dashboard";
import { useScope } from "@/composables/useScope";
import { runtimeLabel, runtimeMessageText } from "@/utils/dashboardText";

const router = useRouter();
const dashboard = useDashboardStore();
const { loadScopes, scopeText } = useScope();

const nodes = computed(() => dashboard.stats?.graph_store.num_nodes ?? null);
const edges = computed(() => dashboard.stats?.graph_store.num_edges ?? null);
const vectors = computed(() => dashboard.stats?.vector_store.num_vectors ?? null);
const dimension = computed(() => dashboard.stats?.vector_store.dimension ?? null);

const queryTrend = computed(() => dashboard.status?.services.query);

const runtimeChip = computed(() => runtimeLabel(dashboard.runtime));
const showRuntimeAlert = computed(() => Boolean(dashboard.runtime && !dashboard.runtime.ok));
const runtimeAlertMessage = computed(() =>
  runtimeMessageText(dashboard.runtime, "运行时环境需要关注"),
);

function goTo(pathOrView: string): void {
  const path = pathOrView.startsWith("/") ? pathOrView : `/${pathOrView}`;
  void router.push(path);
}

function goToImageMemorySettings(): void {
  void router.push({ path: "/settings", query: { group: "image_memory" } });
}

onMounted(async () => {
  await loadScopes();
  await dashboard.refreshAll();
});
</script>

<template>
  <section class="view-dashboard">
    <div class="dashboard-topbar">
      <div class="topbar-meta">
        <span class="tag">作用域：{{ scopeText }}</span>
        <span class="section-label">引擎运行与存储总览</span>
      </div>
      <div class="topbar-actions">
        <button class="btn" type="button" @click="goTo('/images')">浏览图片资产</button>
        <button class="btn" type="button" @click="goTo('/bundles')">管理知识包</button>
        <button class="btn" type="button" @click="goTo('/correction')">纠错与调优</button>
        <button class="btn" type="button" @click="goTo('/memory')">查看事实台账</button>
        <button
          class="btn primary"
          type="button"
          :disabled="dashboard.loading"
          @click="dashboard.refreshAll(true)"
        >
          {{ dashboard.loading ? "刷新中…" : "刷新总览" }}
        </button>
      </div>
    </div>

    <MetricCards
      :nodes="nodes"
      :edges="edges"
      :vectors="vectors"
      :dimension="dimension"
      :runtime="dashboard.runtime"
      :image-status="dashboard.imageStatus"
      @navigate="goTo"
      @configure-image="goToImageMemorySettings"
    />

    <RuntimeBanner
      v-if="showRuntimeAlert"
      :message="runtimeAlertMessage"
      :chip-label="runtimeChip[0]"
      :chip-tone="runtimeChip[1]"
      @refresh="dashboard.loadRuntime(true)"
    />

    <RuntimeOverview @configure-image="goToImageMemorySettings" />

    <ServiceList @navigate="goTo" />

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
  gap: 16px;
  flex: 1;
}

.view-dashboard > :deep(.band),
.view-dashboard > :deep(.notice) {
  margin-top: 0;
}

.dashboard-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.topbar-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.trend-chart-fill {
  flex: 1;
  min-height: 180px;
}
</style>
