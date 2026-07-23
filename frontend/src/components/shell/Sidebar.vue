<script setup lang="ts">
// 侧栏导航：渲染 NAV_ITEMS，高亮当前路由。
// 从 legacy nav（index.html 行 2024-2041）迁移，改用 vue-router 而非 data-view 手动切换。
import { RouterLink } from "vue-router";
import { storeToRefs } from "pinia";
import { NAV_ITEMS } from "@/router";
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const { sidebarCollapsed } = storeToRefs(ui);
</script>

<template>
  <aside class="sidebar" :class="{ collapsed: sidebarCollapsed }">
    <div class="brand">
      <span class="brand-fallback">Memorix</span>
      <button
        class="sidebar-toggle"
        type="button"
        :title="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
        :aria-label="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
        :aria-expanded="!sidebarCollapsed"
        @click="ui.toggleSidebar()"
      >
        {{ sidebarCollapsed ? "›" : "‹" }}
      </button>
    </div>
    <nav class="nav">
      <RouterLink
        v-for="item in NAV_ITEMS"
        :key="item.name"
        :to="item.path"
        class="nav-btn"
        active-class="active"
        :title="sidebarCollapsed ? item.label : undefined"
        :aria-label="item.label"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span class="nav-label">{{ item.label }}</span>
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
  transition: width 0.2s ease, padding 0.2s ease;
}

.sidebar.collapsed {
  width: 72px;
  padding-inline: 10px;
}

.brand {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  height: 44px;
  padding: 0 8px;
  border-radius: var(--radius-md);
  background: var(--nav-soft);
  font-weight: 700;
  color: var(--accent-ink);
  letter-spacing: 1px;
}

.sidebar.collapsed .brand {
  justify-content: center;
  padding: 0;
}

.sidebar.collapsed .brand-fallback,
.sidebar.collapsed .nav-label {
  display: none;
}

.sidebar-toggle {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--hairline);
  border-radius: 9px;
  color: var(--accent-strong);
  background: var(--surface-strong);
  cursor: pointer;
  flex: 0 0 auto;
  font-size: 18px;
  line-height: 1;
}

.sidebar-toggle:hover,
.sidebar-toggle:focus-visible {
  border-color: var(--hairline-strong);
  background: var(--accent-soft);
  outline: none;
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

.sidebar.collapsed .nav-btn {
  justify-content: center;
  padding-inline: 8px;
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
