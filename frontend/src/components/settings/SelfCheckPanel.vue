<script setup lang="ts">
// Runtime 自检面板：状态摘要 + 原始报告。
// 从 legacy renderRuntimeSelfCheck（index.html 行 3169-3184）迁移。
// 数据复用 useDashboardStore.runtime（同源 DRY）；强制刷新上抛。
import { computed } from "vue";
import { useDashboardStore } from "@/stores/dashboard";
import { runtimeLabel, runtimeMessageText, statusLabel } from "@/utils/dashboardText";

const emit = defineEmits<{ force: [] }>();

const dashboard = useDashboardStore();

const report = computed(() => dashboard.runtime);
const [label, tone] = runtimeLabel(report.value);
const toneClass = computed(() => (tone === "ok" ? "ok" : tone === "bad" ? "bad" : "warn"));

const dimension = computed(() => report.value?.embedding?.dimension ?? report.value?.dimension ?? "-");
const expected = computed(
  () => report.value?.embedding?.expected_dimension ?? report.value?.expected_dimension ?? "-",
);
const model = computed(() => report.value?.embedding?.model ?? report.value?.model ?? "-");
const checkedAt = computed(() =>
  report.value?.checked_at
    ? new Date(report.value.checked_at * 1000).toLocaleString("zh-CN")
    : "-",
);
const code = computed(() => report.value?.code || label);
const message = computed(() => runtimeMessageText(report.value, "等待自检"));
const statusText = computed(() => statusLabel(report.value?.ok ? "ready" : "failed"));
const rawJson = computed(() => JSON.stringify(report.value ?? {}, null, 2));
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>Runtime 自检</h2>
      <button class="btn primary" @click="emit('force')">强制刷新</button>
    </div>
    <div class="summary-grid">
      <div class="summary-item"><span>状态</span><strong><span class="status-pill" :class="toneClass">{{ statusText }}</span></strong></div>
      <div class="summary-item"><span>运行信息</span><strong>{{ message }}</strong></div>
      <div class="summary-item"><span>Embedding 维度</span><strong>{{ dimension }} / {{ expected }}</strong></div>
      <div class="summary-item"><span>模型</span><strong>{{ model }}</strong></div>
      <div class="summary-item"><span>检查时间</span><strong>{{ checkedAt }}</strong></div>
      <div class="summary-item"><span>报告代码</span><strong>{{ code }}</strong></div>
    </div>
    <details class="advanced-panel">
      <summary>自检原始报告</summary>
      <pre class="json" style="margin-top: 10px">{{ rawJson }}</pre>
    </details>
  </div>
</template>
