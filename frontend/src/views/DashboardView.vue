<script setup lang="ts">
// Dashboard 总览视图：4 核心指标卡 + 快捷工作台入口 + 运行概况 + 服务分区列表 + 调用趋势图。
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import MetricCards from "@/components/dashboard/MetricCards.vue";
import RuntimeBanner from "@/components/dashboard/RuntimeBanner.vue";
import RuntimeOverview from "@/components/dashboard/RuntimeOverview.vue";
import ServiceList from "@/components/dashboard/ServiceList.vue";
import CallTrendChart from "@/components/dashboard/CallTrendChart.vue";
import NavIcons from "@/components/shell/NavIcons.vue";
import { useDashboardStore } from "@/stores/dashboard";
import { useGraphStore } from "@/stores/graph";
import { useMemoryStore } from "@/stores/memory";
import { useScope } from "@/composables/useScope";
import {
  deriveDualPoolUpgradeNotice,
  runtimeLabel,
  runtimeMessageText,
} from "@/utils/dashboardText";
import { safeStorage } from "@/utils/storage";

const router = useRouter();
const dashboard = useDashboardStore();
const graph = useGraphStore();
const memory = useMemoryStore();
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

const dualUpgrade = computed(() =>
  deriveDualPoolUpgradeNotice(dashboard.stats, dashboard.runtime),
);

function upgradeDismissKey(scope: string): string {
  return `memorix.dismiss_dual_upgrade.${scope || "default"}`;
}

const upgradeDismissed = ref(
  safeStorage.getItem(upgradeDismissKey(graph.effectiveScope())) === "1",
);

watch(
  () => graph.effectiveScope(),
  (scope) => {
    upgradeDismissed.value = safeStorage.getItem(upgradeDismissKey(scope)) === "1";
  },
);

const legacySummaryParts = computed(() => {
  const parts: string[] = [];
  if (dualUpgrade.value.legacyVectors > 0) {
    parts.push(`${dualUpgrade.value.legacyVectors} 条单池向量`);
  }
  if (dualUpgrade.value.paragraphs > 0) {
    parts.push(`${dualUpgrade.value.paragraphs} 条原文段落`);
  }
  if (dualUpgrade.value.nodes > 0) {
    parts.push(`${dualUpgrade.value.nodes} 个图谱实体`);
  }
  return parts.length ? parts.join(" · ") : "检测到历史记忆数据";
});

function dismissUpgradeBanner(): void {
  upgradeDismissed.value = true;
  safeStorage.setItem(upgradeDismissKey(graph.effectiveScope()), "1");
}

async function runDualUpgradeNow(): Promise<void> {
  const ok = await memory.rebuildAllVectors();
  if (ok) {
    safeStorage.removeItem(upgradeDismissKey(graph.effectiveScope()));
    upgradeDismissed.value = false;
    await dashboard.refreshAll(true);
  }
}

function goToVectorMaintenance(): void {
  void router.push({ path: "/memory", query: { section: "vectors" } });
}

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

    <div
      v-if="dualUpgrade.needed && !upgradeDismissed"
      class="upgrade-banner"
      role="region"
      aria-label="v2.0 双向量池升级引导"
    >
      <div class="upgrade-banner-main">
        <span class="icon-badge amber upgrade-icon" aria-hidden="true">
          <NavIcons name="vector" :size="18" />
        </span>
        <div class="upgrade-copy">
          <div class="upgrade-title-row">
            <span class="tag warn">旧版升级引导</span>
            <strong class="tabular">
              当前作用域尚未启用 v2.0 双向量池（{{ legacySummaryParts }}）
            </strong>
          </div>
          <p>
            检测到从旧版本升级的单池记忆数据，当前正以单池兼容模式运行。点击「立即重建双池向量」即可在线分离原文段落池与图谱实体池，提升混合检索均衡度。
          </p>
        </div>
      </div>

      <div class="upgrade-actions">
        <button
          class="btn primary"
          type="button"
          :disabled="memory.vectorBusy"
          @click="runDualUpgradeNow"
        >
          {{ memory.vectorBusy ? "正在重建双池向量…" : "立即重建双池向量" }}
        </button>
        <button
          class="btn"
          type="button"
          :disabled="memory.vectorBusy"
          @click="goToVectorMaintenance"
        >
          查看向量面板
        </button>
        <button
          class="btn ghost sm"
          type="button"
          :disabled="memory.vectorBusy"
          @click="dismissUpgradeBanner"
        >
          暂不提示
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

.upgrade-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 16px 18px;
  border: 1px solid var(--amber);
  border-left-width: 4px;
  border-radius: var(--radius-lg);
  background: var(--panel-bg);
}

.upgrade-banner-main {
  flex: 1 1 360px;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.upgrade-icon {
  flex-shrink: 0;
  margin-top: 2px;
}

.upgrade-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.upgrade-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.upgrade-title-row strong {
  color: var(--ink);
  font-size: 14.5px;
  font-weight: 700;
  line-height: 1.4;
}

.upgrade-copy p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}

.upgrade-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
