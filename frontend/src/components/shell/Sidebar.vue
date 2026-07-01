<script setup lang="ts">
// 侧栏导航：渲染 NAV_ITEMS，高亮当前路由。
// 从 legacy nav（index.html 行 2024-2041）迁移，改用 vue-router 而非 data-view 手动切换。
import { RouterLink } from "vue-router";
import { NAV_ITEMS } from "@/router";
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-fallback">M</span>
    </div>
    <nav class="nav">
      <RouterLink
        v-for="item in NAV_ITEMS"
        :key="item.name"
        :to="item.path"
        class="nav-btn"
        active-class="active"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 14px;
  width: 188px;
  flex-shrink: 0;
  background: var(--nav);
  backdrop-filter: var(--blur);
  border-right: 1px solid var(--hairline);
}

.brand {
  display: grid;
  place-items: center;
  height: 44px;
  border-radius: var(--radius-md);
  background: var(--nav-soft);
  font-weight: 700;
  color: var(--accent-ink);
  letter-spacing: 1px;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
}

.nav-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  color: var(--muted);
  text-decoration: none;
  font-size: 14px;
  transition: background 0.15s, color 0.15s;
}

.nav-btn:hover {
  background: var(--nav-soft);
  color: var(--text);
}

.nav-btn.active {
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-weight: 600;
}

.nav-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 8px;
  background: var(--surface-strong);
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}
</style>
