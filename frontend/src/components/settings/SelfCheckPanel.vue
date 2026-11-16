<script setup lang="ts">
// Runtime 自检面板（v2.0）：展示 Embedding 维度核验与双向量池（configured_mode / effective_mode / paragraph_pool / graph_pool）状态。
import { computed } from "vue";
import type { VectorPoolStatus } from "@/services/configApi";
import { useDashboardStore } from "@/stores/dashboard";
import { runtimeLabel, runtimeMessageText, statusLabel } from "@/utils/dashboardText";

const emit = defineEmits<{ force: [] }>();

const dashboard = useDashboardStore();

const report = computed(() => dashboard.runtime);
const runtimeTuple = computed(() => runtimeLabel(report.value));
const label = computed(() => runtimeTuple.value[0]);
const tone = computed(() => runtimeTuple.value[1]);
const toneClass = computed(() => (tone.value === "ok" ? "ok" : tone.value === "bad" ? "bad" : "warn"));

const actualDim = computed(
  () =>
    report.value?.encoded_dimension ??
    report.value?.detected_dimension ??
    report.value?.embedding?.dimension ??
    report.value?.dimension ??
    "-",
);
const configuredDim = computed(
  () =>
    report.value?.configured_dimension ??
    report.value?.requested_dimension ??
    report.value?.embedding?.expected_dimension ??
    report.value?.expected_dimension ??
    "-",
);
const storeDim = computed(() => report.value?.vector_store_dimension ?? "-");
const elapsedText = computed(() =>
  typeof report.value?.elapsed_ms === "number" ? `${report.value.elapsed_ms.toFixed(1)} ms` : "-",
);
const checkedAt = computed(() =>
  report.value?.checked_at
    ? new Date(report.value.checked_at * 1000).toLocaleString("zh-CN", { hour12: false })
    : "-",
);
const code = computed(() => report.value?.code || label.value);
const message = computed(() => runtimeMessageText(report.value, "等待自检"));
const statusText = computed(() => statusLabel(report.value?.ok ? "ready" : "failed"));

const pools = computed(() => report.value?.vector_pools || null);
const isPoolDegraded = computed(() => {
  if (!pools.value) return false;
  return pools.value.configured_mode === "dual" && pools.value.effective_mode !== "dual";
});

function poolSummaryText(pool: VectorPoolStatus | null | undefined): string {
  if (!pool || !pool.available) return "未挂载";
  const count = typeof pool.num_vectors === "number" ? `${pool.num_vectors} 条` : "0 条";
  const dim = pool.dimension != null ? `维度 ${pool.dimension}` : "维度待定";
  return `${count} · ${dim}`;
}

const rawJson = computed(() => JSON.stringify(report.value ?? {}, null, 2));
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>运行时与双向量池自检</h2>
        <span class="section-label">探测 Embedding 实际输出维度与双池索引挂载状态</span>
      </div>
      <button class="btn primary" type="button" @click="emit('force')">
        重新运行自检
      </button>
    </div>

    <div class="check-kv-grid">
      <div class="summary-item">
        <span>健康状态</span>
        <strong><span class="status-pill" :class="toneClass">{{ statusText }}</span></strong>
      </div>
      <div class="summary-item">
        <span>诊断结论</span>
        <strong>{{ message }}</strong>
      </div>
      <div class="summary-item">
        <span>编码维度 / 配置维度</span>
        <strong class="mono tabular-nums">{{ actualDim }} / {{ configuredDim }}</strong>
      </div>
      <div class="summary-item">
        <span>主索引存储维度</span>
        <strong class="mono tabular-nums">{{ storeDim }}</strong>
      </div>
      <div class="summary-item">
        <span>探测耗时 / 状态码</span>
        <strong class="mono tabular-nums">{{ elapsedText }} · {{ code }}</strong>
      </div>
      <div class="summary-item">
        <span>最近检查时间</span>
        <strong class="tabular-nums">{{ checkedAt }}</strong>
      </div>
    </div>

    <!-- v2.0 双向量池诊断区（扁平分割，不再套第三层框） -->
    <div v-if="pools" class="pools-section">
      <div class="pools-head">
        <span class="section-label">向量池架构状态（vector_pools）</span>
        <div class="tags" style="margin-top: 0">
          <span class="tag mono">配置：{{ pools.configured_mode || "-" }}</span>
          <span
            class="tag mono"
            :class="pools.effective_mode === 'dual' ? 'ok' : isPoolDegraded ? 'warn' : ''"
          >
            生效：{{ pools.effective_mode || "-" }}
          </span>
          <span class="tag" :class="pools.ready ? 'ok' : 'warn'">
            {{ pools.ready ? "双池已就绪" : "单池兼容运行" }}
          </span>
        </div>
      </div>

      <p v-if="isPoolDegraded" class="degrade-hint">
        当前配置为 <code class="mono">dual</code>，尚未检测到双池迁移标记，已自动回退 <code class="mono">single</code> 单池运行。
      </p>

      <div class="check-kv-grid pools-grid">
        <div class="summary-item">
          <span>段落向量池（paragraph_pool）</span>
          <strong class="mono tabular-nums">{{ poolSummaryText(pools.paragraph_pool) }}</strong>
        </div>
        <div class="summary-item">
          <span>图谱向量池（graph_pool）</span>
          <strong class="mono tabular-nums">{{ poolSummaryText(pools.graph_pool) }}</strong>
        </div>
        <div class="summary-item">
          <span>单池兼容索引（single_pool）</span>
          <strong class="mono tabular-nums">{{ poolSummaryText(pools.single_pool) }}</strong>
        </div>
      </div>
    </div>

    <details class="advanced-panel">
      <summary>展开原始自检报告 JSON</summary>
      <pre class="json" style="margin-top: 10px">{{ rawJson }}</pre>
    </details>
  </div>
</template>

<style scoped>
.tabular-nums {
  font-variant-numeric: tabular-nums;
}

.check-kv-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.pools-section {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
}

.pools-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.degrade-hint {
  margin: 0 0 10px;
  padding: 8px 12px;
  font-size: 12.5px;
  color: var(--amber);
  background: var(--amber-soft);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.pools-grid {
  margin-top: 4px;
}

@media (max-width: 960px) {
  .check-kv-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .check-kv-grid {
    grid-template-columns: 1fr;
  }
}
</style>
