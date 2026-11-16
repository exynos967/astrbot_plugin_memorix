<script setup lang="ts">
// 全局顶栏：左侧展示当前视图标题与副标题（含窄屏抽屉开关），
// 右侧提供全局作用域选择器与快捷主题切换（跟随 / 亮色 / 暗色）。
import { computed } from "vue";
import { useRoute } from "vue-router";
import { storeToRefs } from "pinia";
import { useScope } from "@/composables/useScope";
import { useGraphStore } from "@/stores/graph";
import { useUiStore, type UiTheme } from "@/stores/ui";
import NavIcons from "./NavIcons.vue";

const route = useRoute();
const graph = useGraphStore();
const ui = useUiStore();
const { changeScope, loadScopes } = useScope();

const { currentScope, resolvedScope, scopeOptions } = storeToRefs(graph);
const { theme, mobileNavOpen } = storeToRefs(ui);

const title = computed(() => (route.meta.title as string) ?? "");
const subtitle = computed(() => (route.meta.subtitle as string) ?? "");

const autoScopeLabel = computed(() => {
  if (!resolvedScope.value) return "自动（最近活跃）";
  const hit = scopeOptions.value.find((s) => s.value === resolvedScope.value);
  const short = hit ? hit.label : resolvedScope.value;
  return `自动（${short}）`;
});

const THEME_OPTIONS: Array<{ value: UiTheme; label: string; title: string }> = [
  { value: "auto", label: "跟随", title: "跟随 AstrBot 宿主或系统主题" },
  { value: "light", label: "亮色", title: "使用浅色主题" },
  { value: "dark", label: "暗色", title: "使用深色主题" },
];

function onScopeChange(e: Event): void {
  const val = (e.target as HTMLSelectElement).value;
  changeScope(val);
}
</script>

<template>
  <header class="topbar">
    <div class="topbar-left">
      <button
        class="mobile-menu-btn"
        type="button"
        title="切换导航菜单"
        aria-label="切换导航菜单"
        :aria-expanded="mobileNavOpen"
        @click="ui.toggleMobileNav()"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      </button>
      <div class="view-title">
        <h1>{{ title }}</h1>
        <p v-if="subtitle">{{ subtitle }}</p>
      </div>
    </div>

    <div class="topbar-controls">
      <div class="scope-control">
        <label class="scope-caption" for="topbar-scope-select">当前作用域</label>
        <div class="scope-select-wrap">
          <select
            id="topbar-scope-select"
            class="select scope-select"
            :value="currentScope"
            aria-label="切换当前记忆作用域"
            @change="onScopeChange"
          >
            <option value="">{{ autoScopeLabel }}</option>
            <option v-for="opt in scopeOptions" :key="opt.value" :value="opt.value">
              {{ opt.label || opt.value }}
            </option>
          </select>
          <button
            class="scope-refresh-btn"
            type="button"
            title="刷新可选作用域"
            aria-label="刷新可选作用域"
            @click="loadScopes()"
          >
            <NavIcons name="refresh" :size="15" />
          </button>
        </div>
      </div>

      <div class="theme-switcher" role="group" aria-label="界面主题切换">
        <button
          v-for="opt in THEME_OPTIONS"
          :key="opt.value"
          type="button"
          class="theme-chip"
          :class="{ active: theme === opt.value }"
          :aria-pressed="theme === opt.value"
          :title="opt.title"
          @click="ui.setTheme(opt.value)"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  min-width: 0;
  max-width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px 14px;
  padding: 12px 24px;
  border-bottom: 1px solid var(--border-color);
  background: var(--shell);
}

.topbar-left {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1 1 180px;
}

.mobile-menu-btn {
  display: none;
  width: 38px;
  height: 38px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--control-bg);
  color: var(--ink);
  place-items: center;
  cursor: pointer;
  flex-shrink: 0;
  transition:
    border-color var(--m-out) var(--ease),
    background-color var(--m-out) var(--ease);
}

.mobile-menu-btn:hover {
  border-color: var(--border-hover);
  background: var(--surface-soft);
}

.view-title {
  min-width: 0;
}

.view-title h1 {
  margin: 0;
  font-size: 18px;
  line-height: 1.25;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ink);
}

.view-title p {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 0;
}

.scope-control {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.scope-caption {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--muted);
  white-space: nowrap;
}

.scope-select-wrap {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.scope-select {
  min-height: 36px;
  height: 36px;
  min-width: 176px;
  max-width: 260px;
  padding: 4px 10px;
  border-radius: var(--radius-md);
  font-size: 13px;
}

.scope-refresh-btn {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--control-bg);
  color: var(--muted);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition:
    color var(--m-out) var(--ease),
    border-color var(--m-out) var(--ease),
    background-color var(--m-out) var(--ease),
    transform var(--m-out) var(--ease);
}

@media (hover: hover) and (pointer: fine) {
  .scope-refresh-btn:hover {
    color: var(--ink);
    border-color: var(--border-hover);
    background: var(--surface-soft);
    transform: translateY(-1px);
    transition-duration: var(--m-in);
  }
}

.scope-refresh-btn:active {
  transform: translateY(1px) scale(0.985);
  transition-duration: var(--m-press);
}

.theme-switcher {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--surface-soft);
}

.theme-chip {
  min-height: 30px;
  padding: 3px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition:
    color var(--m-out) var(--ease),
    background-color var(--m-out) var(--ease),
    border-color var(--m-out) var(--ease);
}

.theme-chip:hover {
  color: var(--ink);
}

.theme-chip.active {
  color: var(--ink);
  background: var(--panel-bg);
  border-color: var(--border-color);
  box-shadow: var(--shadow-xs);
}

@media (max-width: 1160px) {
  .topbar {
    padding: 12px 18px;
  }

  .scope-caption {
    display: none;
  }

  .scope-select {
    min-width: 132px;
    max-width: 200px;
  }
}

@media (max-width: 860px) {
  .topbar {
    padding: 12px 16px;
  }

  .mobile-menu-btn {
    display: grid;
  }

  .scope-select {
    min-width: 132px;
    max-width: 196px;
    min-height: 38px;
    height: 38px;
  }

  .scope-refresh-btn {
    width: 38px;
    height: 38px;
  }

  .theme-chip {
    min-height: 32px;
  }
}

@media (max-width: 640px) {
  .topbar {
    padding: 10px 12px;
    gap: 10px;
  }

  .topbar-left {
    flex: 1 1 100%;
  }

  .mobile-menu-btn {
    width: 40px;
    height: 40px;
  }

  .topbar-controls {
    width: 100%;
    justify-content: space-between;
    gap: 8px;
  }

  .scope-control {
    flex: 1 1 0;
  }

  .scope-select-wrap {
    width: 100%;
  }

  .scope-select {
    flex: 1 1 0;
    width: 100%;
    min-width: 0;
    max-width: none;
  }
}
</style>
