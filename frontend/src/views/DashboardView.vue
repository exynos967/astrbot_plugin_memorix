<script setup lang="ts">
// P0 占位 view：仅验证 Vue 应用挂载 + AstrBot 托管链路。
// P2 起替换为真实 Dashboard（stats / auth / 调用趋势，并修复 C5/C2）。
import { computed } from "vue";
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const title = computed(() => "Memorix Dashboard");
const subtitle = computed(() => "Vue3 + Vite + Pinia 重构中 · P0 工程骨架");
</script>

<template>
  <main class="dashboard-placeholder">
    <header class="placeholder-head">
      <h1>{{ title }}</h1>
      <p>{{ subtitle }}</p>
      <p class="placeholder-meta">
        当前主题：<code>{{ ui.theme }}</code> · 特效：<code>{{ ui.effects }}</code>
      </p>
    </header>
    <section class="placeholder-body">
      <p>这是 Vue3 重构的工程骨架占位页。P1 起将逐步移植 10 个 view。</p>
      <p>构建链：<code>vite build</code> → <code>pages/memorix-vue/</code> → AstrBot 托管。</p>
    </section>
  </main>
</template>

<style scoped>
.dashboard-placeholder {
  height: 100%;
  padding: 48px;
  display: flex;
  flex-direction: column;
  gap: 24px;
  overflow: auto;
}

.placeholder-head h1 {
  margin: 0 0 6px;
  font-size: 28px;
  color: var(--ink);
}

.placeholder-head p {
  margin: 2px 0;
  color: var(--muted);
}

.placeholder-meta code {
  background: var(--accent-soft);
  padding: 1px 6px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--accent-strong);
}

.placeholder-body {
  padding: 24px;
  border-radius: var(--radius-lg);
  background: var(--surface);
  backdrop-filter: var(--blur-soft);
  border: 1px solid var(--hairline);
  color: var(--text);
  line-height: 1.8;
}
</style>
