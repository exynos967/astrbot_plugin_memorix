import { defineStore } from "pinia";
import { ref } from "vue";
import { readJSON, writeJSON } from "@/utils/storage";

export type UiTheme = "auto" | "light" | "dark";
export type UiEffects = "glass" | "lite";

const UI_PREFS_KEY = "memorix.webui.ui_prefs";

interface UiPrefs {
  theme: UiTheme;
  effects: UiEffects;
  sidebarCollapsed: boolean;
}

const DEFAULT_PREFS: UiPrefs = { theme: "auto", effects: "glass", sidebarCollapsed: false };

/**
 * UI 偏好 store：主题/特效 + localStorage 持久化。
 * P0 最小骨架：theme/effects 字段 + 持久化读写（经 safeStorage 修 H7）。
 * view 字段（当前路由）由 vue-router 管理，不在此处重复。
 */
export const useUiStore = defineStore("ui", () => {
  const prefs = ref<UiPrefs>(readJSON<UiPrefs>(UI_PREFS_KEY, { ...DEFAULT_PREFS }));
  // 容错：旧数据可能缺少字段
  if (!prefs.value.theme) prefs.value.theme = DEFAULT_PREFS.theme;
  if (!prefs.value.effects) prefs.value.effects = DEFAULT_PREFS.effects;
  if (typeof prefs.value.sidebarCollapsed !== "boolean") {
    prefs.value.sidebarCollapsed = DEFAULT_PREFS.sidebarCollapsed;
  }

  const theme = ref<UiTheme>(prefs.value.theme);
  const effects = ref<UiEffects>(prefs.value.effects);
  const sidebarCollapsed = ref(prefs.value.sidebarCollapsed);

  function persist(): void {
    writeJSON(UI_PREFS_KEY, {
      theme: theme.value,
      effects: effects.value,
      sidebarCollapsed: sidebarCollapsed.value,
    });
  }

  function setTheme(next: UiTheme): void {
    theme.value = next;
    persist();
  }

  function setEffects(next: UiEffects): void {
    effects.value = next;
    persist();
  }

  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value;
    persist();
  }

  return {
    theme,
    effects,
    sidebarCollapsed,
    setTheme,
    setEffects,
    toggleSidebar,
    persist,
  };
});
