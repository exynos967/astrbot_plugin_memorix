<script setup lang="ts">
// 顶栏：显示当前路由标题/副标题（从 route.meta 读取）。
// 从 legacy topbar（index.html 行 2044-2049）迁移，替换 #page-title/#page-subtitle 手动写入。
import { useRoute } from "vue-router";
import { computed } from "vue";

const route = useRoute();
const title = computed(() => (route.meta.title as string) ?? "");
const subtitle = computed(() => (route.meta.subtitle as string) ?? "");
</script>

<template>
  <header class="topbar">
    <div class="view-title">
      <h1>{{ title }}</h1>
      <p>{{ subtitle }}</p>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  padding: 18px 28px;
  border-bottom: 1px solid var(--hairline);
  background: var(--shell);
  backdrop-filter: var(--blur-soft);
}

.view-title h1 {
  margin: 0;
  font-size: 20px;
  color: var(--ink);
}

.view-title p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}
</style>
