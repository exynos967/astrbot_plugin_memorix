<script setup lang="ts">
// 占位 view：未到对应阶段（P2-P8）的 view 临时显示此页。
// 标题/副标题从路由 meta 注入。
import { useRoute } from "vue-router";
import { computed } from "vue";

const route = useRoute();
const title = computed(() => (route.meta.title as string) ?? "建设中");
const subtitle = computed(() => (route.meta.subtitle as string) ?? "");
</script>

<template>
  <section class="placeholder-view">
    <div class="placeholder-card">
      <h2>{{ title }}</h2>
      <p class="placeholder-sub">{{ subtitle }}</p>
      <p class="placeholder-state">该页面待 Vue 重构对应阶段完成，请暂用 legacy 页面。</p>
    </div>
  </section>
</template>

<style scoped>
.placeholder-view {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
}

.placeholder-card {
  padding: 40px 48px;
  border-radius: var(--radius-lg);
  background: var(--surface);
  backdrop-filter: var(--blur-soft);
  border: 1px solid var(--hairline);
  text-align: center;
  max-width: 480px;
}

.placeholder-card h2 {
  margin: 0 0 8px;
  color: var(--ink);
}

.placeholder-sub {
  margin: 0 0 16px;
  color: var(--muted);
}

.placeholder-state {
  margin: 0;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-size: 13px;
}
</style>
