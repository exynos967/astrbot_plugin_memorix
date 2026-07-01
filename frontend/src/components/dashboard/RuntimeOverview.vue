<script setup lang="ts">
// 运行概况 band：鉴权态 + 自动保存指示。
// 从 legacy runtime-grid（index.html 行 2073-2085, 2761-2778）迁移。
// 鉴权态：能进入 AstrBot 插件页 iframe 即已鉴权（与 legacy updateDashboardAuthState 一致）。
// 自动保存：读 useDashboardStore.config.auto_save_enabled（P2 仅显示，开关在 P3 Settings）。
import { computed } from "vue";
import { useDashboardStore } from "@/stores/dashboard";
import { runtimeLabel } from "@/utils/dashboardText";

const dashboard = useDashboardStore();

const autoSaveText = computed(() =>
  dashboard.config?.auto_save_enabled ? "auto-save on" : "auto-save -",
);
const autoSaveTone = computed(() => (dashboard.config?.auto_save_enabled ? "ok" : "warn"));

// runtime-chip 标签/语气：必须包进 computed，否则 setup 顶层求值一次后不再随 dashboard.runtime 刷新。
const chip = computed(() => runtimeLabel(dashboard.runtime));
const chipLabel = computed(() => chip.value[0]);
const chipToneClass = computed(() => {
  const t = chip.value[1];
  return t === "ok" ? "ok" : t === "bad" ? "bad" : "warn";
});
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>运行概况</h2>
      <span class="tag" :class="chipToneClass">{{ chipLabel }}</span>
    </div>
    <div class="runtime-grid">
      <span class="tag">API /api</span>
      <span class="tag">API /v1</span>
      <span class="tag ok">Dashboard 已鉴权</span>
      <span class="tag ok">Dashboard 已鉴权</span>
      <span class="tag" :class="autoSaveTone">{{ autoSaveText }}</span>
    </div>
  </div>
</template>
