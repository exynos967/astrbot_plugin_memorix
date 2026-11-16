<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";

const route = useRoute();
const title = computed(() => (route.meta.title as string) ?? "模块未就绪");
const subtitle = computed(() => (route.meta.subtitle as string) ?? "");
</script>

<template>
  <section class="placeholder-view">
    <div class="placeholder-card">
      <h2>{{ title }}</h2>
      <p v-if="subtitle" class="placeholder-sub">{{ subtitle }}</p>
      <p class="placeholder-state">当前模块尚未挂载。</p>
    </div>
  </section>
</template>

<style scoped>
.placeholder-view {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.placeholder-card {
  width: min(460px, 100%);
  padding: 32px 36px;
  border-radius: var(--radius-lg);
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  text-align: center;
}

.placeholder-card h2 {
  margin: 0 0 8px;
  color: var(--ink);
  font-size: 17px;
  font-weight: 650;
}

.placeholder-sub {
  margin: 0 0 16px;
  color: var(--muted);
  font-size: 13.5px;
}

.placeholder-state {
  margin: 0;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  background: var(--surface-soft);
  border: 1px solid var(--hairline);
  color: var(--muted);
  font-size: 13px;
}
</style>
