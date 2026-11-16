<script setup lang="ts">
// 侧栏分组导航：4 大业务分组、13 个路由，支持桌面端折叠与移动端抽屉遮罩（含 Esc 关闭）。
import { onBeforeUnmount, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { storeToRefs } from "pinia";
import { NAV_GROUPS } from "@/router";
import { useUiStore } from "@/stores/ui";
import NavIcons from "./NavIcons.vue";

const ui = useUiStore();
const { sidebarCollapsed, mobileNavOpen } = storeToRefs(ui);

function onKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape" && mobileNavOpen.value) {
    ui.setMobileNav(false);
  }
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <div
    v-if="mobileNavOpen"
    class="sidebar-backdrop"
    aria-hidden="true"
    @click="ui.setMobileNav(false)"
  />
  <aside
    class="sidebar"
    :class="{ collapsed: sidebarCollapsed, 'mobile-open': mobileNavOpen }"
    aria-label="主导航"
  >
    <div class="brand">
      <div class="brand-identity">
        <span class="brand-logo">
          <NavIcons name="brand" :size="16" />
        </span>
        <div class="brand-copy">
          <strong>Memorix</strong>
          <small>长期记忆控制台</small>
        </div>
      </div>
      <button
        class="sidebar-toggle"
        type="button"
        :title="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
        :aria-label="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
        :aria-expanded="!sidebarCollapsed"
        @click="ui.toggleSidebar()"
      >
        <NavIcons :name="sidebarCollapsed ? 'chevron-right' : 'chevron-left'" :size="15" />
      </button>
    </div>

    <nav class="nav">
      <div v-for="group in NAV_GROUPS" :key="group.id" class="nav-group">
        <div class="nav-group-label">{{ group.label }}</div>
        <RouterLink
          v-for="item in group.items"
          :key="item.name"
          :to="item.path"
          class="nav-btn"
          active-class="active"
          :title="sidebarCollapsed ? `${item.label} — ${item.subtitle}` : undefined"
          :aria-label="item.label"
          @click="ui.setMobileNav(false)"
        >
          <span class="nav-icon">
            <NavIcons :name="item.icon" :size="17" />
          </span>
          <span class="nav-label">{{ item.label }}</span>
          <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
        </RouterLink>
      </div>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 14px 12px;
  width: 212px;
  flex-shrink: 0;
  background: var(--nav);
  border-right: 1px solid var(--border-color);
  transition:
    width var(--m-panel) var(--ease),
    padding var(--m-panel) var(--ease),
    transform var(--m-panel) var(--ease);
  z-index: 40;
}

.sidebar.collapsed {
  width: 66px;
  padding-inline: 10px;
}

.brand {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 42px;
  padding: 4px 6px 12px;
  border-bottom: 1px solid var(--hairline);
}

.brand-identity {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 9px;
}

.brand-logo {
  width: 28px;
  height: 28px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  color: var(--on-primary);
  background: var(--primary-fill);
  flex-shrink: 0;
}

.brand-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: 1.22;
}

.brand-copy strong {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.01em;
}

.brand-copy small {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.sidebar.collapsed .brand {
  justify-content: center;
  padding-inline: 2px;
}

.sidebar.collapsed .brand-identity,
.sidebar.collapsed .nav-label,
.sidebar.collapsed .nav-badge,
.sidebar.collapsed .nav-group-label {
  display: none;
}

.sidebar-toggle {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  color: var(--muted);
  background: var(--control-bg);
  cursor: pointer;
  flex: 0 0 auto;
  transition:
    color var(--m-out) var(--ease),
    border-color var(--m-out) var(--ease),
    background-color var(--m-out) var(--ease);
}

.sidebar-toggle:hover {
  color: var(--ink);
  border-color: var(--border-hover);
  background: var(--surface-soft);
}

.sidebar-toggle:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.nav {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  scrollbar-width: thin;
  padding-right: 2px;
}

.nav-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar.collapsed .nav-group {
  padding-top: 8px;
  border-top: 1px solid var(--hairline);
}

.sidebar.collapsed .nav-group:first-child {
  padding-top: 0;
  border-top: 0;
}

.nav-group-label {
  padding: 0 10px 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-2);
  user-select: none;
}

.nav-btn {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 36px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  color: var(--muted);
  text-decoration: none;
  font-size: 13.5px;
  font-weight: 500;
  transition:
    background-color var(--m-out) var(--ease),
    color var(--m-out) var(--ease);
}

.sidebar.collapsed .nav-btn {
  justify-content: center;
  padding-inline: 6px;
}

.nav-btn:hover {
  background: var(--nav-soft);
  color: var(--ink);
}

.nav-btn.active {
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-weight: 600;
}

.nav-btn.active::before {
  position: absolute;
  left: 0;
  top: 7px;
  bottom: 7px;
  width: 2.5px;
  border-radius: 999px;
  background: var(--accent);
  content: "";
}

.nav-icon {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  color: currentColor;
  flex-shrink: 0;
}

.nav-btn.active .nav-icon {
  color: var(--accent);
}

.nav-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-badge {
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  color: var(--accent-ink);
  background: var(--accent-soft);
  border: 1px solid var(--hairline-strong);
}

.sidebar-backdrop {
  display: none;
}

@media (max-width: 860px) {
  .sidebar-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 80;
    background: var(--bg-overlay);
  }

  .sidebar {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    width: 244px !important;
    padding: 16px 14px !important;
    z-index: 90;
    transform: translateX(-104%);
    box-shadow: var(--shadow);
  }

  .sidebar.mobile-open {
    transform: translateX(0);
  }

  .sidebar.collapsed .brand {
    justify-content: space-between;
    padding-inline: 6px;
  }

  .sidebar.collapsed .brand-identity {
    display: flex;
  }

  .sidebar.collapsed .nav-label,
  .sidebar.collapsed .nav-group-label,
  .sidebar.collapsed .nav-badge {
    display: inline-block;
  }

  .sidebar.collapsed .nav-group {
    padding-top: 0;
    border-top: 0;
  }

  .sidebar.collapsed .nav-btn {
    justify-content: flex-start;
    padding-inline: 10px;
  }

  .nav-btn {
    min-height: 40px;
  }

  .sidebar-toggle {
    display: none;
  }
}
</style>
