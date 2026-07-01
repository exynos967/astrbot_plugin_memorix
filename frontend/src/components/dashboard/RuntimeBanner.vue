<script setup lang="ts">
// 运行时自检横幅：显示自检结果摘要 + 刷新按钮。
// 从 legacy notice + runtime-banner（index.html 行 2068-2071, 3186-3203）迁移。
// 纯展示 + 事件上抛（emit refresh），自检数据/动作在 useDashboardStore。

defineProps<{
  /** 自检结果文案（runtimeMessageText 派生）。 */
  message: string;
  /** runtime-chip 标签（runtimeLabel 第一项）。 */
  chipLabel: string;
  /** runtime-chip 语气（runtimeLabel 第二项 → tag ok/warn/bad）。 */
  chipTone: "ok" | "warn" | "bad";
}>();

const emit = defineEmits<{ refresh: [] }>();
</script>

<template>
  <div class="notice" style="margin-top: 16px">
    <strong>{{ message }}</strong>
    <button class="btn icon" title="刷新自检" @click="emit('refresh')">↻</button>
  </div>
</template>
