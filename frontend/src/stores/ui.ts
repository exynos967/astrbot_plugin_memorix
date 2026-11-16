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
 * UI 偏好 store：主题/特效/侧栏折叠态 + safeStorage 持久化 + 窄屏抽屉导航开关。
 */
export const useUiStore = defineStore("ui", () => {
  const prefs = ref<UiPrefs>(readJSON<UiPrefs>(UI_PREFS_KEY, { ...DEFAULT_PREFS }));
  if (!prefs.value.theme) prefs.value.theme = DEFAULT_PREFS.theme;
  if (!prefs.value.effects) prefs.value.effects = DEFAULT_PREFS.effects;
  if (typeof prefs.value.sidebarCollapsed !== "boolean") {
    prefs.value.sidebarCollapsed = DEFAULT_PREFS.sidebarCollapsed;
  }

  const theme = ref<UiTheme>(prefs.value.theme);
  const effects = ref<UiEffects>(prefs.value.effects);
  const sidebarCollapsed = ref(prefs.value.sidebarCollapsed);
  const mobileNavOpen = ref(false);

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

  function cycleTheme(): void {
    const order: UiTheme[] = ["auto", "light", "dark"];
    const idx = order.indexOf(theme.value);
    setTheme(order[(idx + 1) % order.length]);
  }

  function setEffects(next: UiEffects): void {
    effects.value = next;
    persist();
  }

  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value;
    persist();
  }

  function setMobileNav(open: boolean): void {
    mobileNavOpen.value = open;
  }

  function toggleMobileNav(): void {
    mobileNavOpen.value = !mobileNavOpen.value;
  }

  return {
    theme,
    effects,
    sidebarCollapsed,
    mobileNavOpen,
    setTheme,
    cycleTheme,
    setEffects,
    toggleSidebar,
    setMobileNav,
    toggleMobileNav,
    persist,
  };
});
