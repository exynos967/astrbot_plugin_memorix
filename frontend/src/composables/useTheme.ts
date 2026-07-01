import { computed, onScopeDispose, watch } from "vue";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";

/**
 * 主题/特效同步：watch useUiStore.theme/effects，写 document.documentElement.dataset。
 * theme="auto" 时解析 prefers-color-scheme 并监听变化（修复 legacy auto 分支）。
 * 从 legacy applyUiPrefs（行 2642-2643）+ resolvedUiTheme（行 2625-2628）+ matchMedia change（行 4882）提取。
 */
export function useTheme() {
  const store = useUiStore();
  const { theme, effects } = storeToRefs(store);

  const prefersDark =
    typeof window !== "undefined" && window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;

  const resolvedTheme = computed<"light" | "dark">(() => {
    if (theme.value === "auto") {
      return prefersDark?.matches ? "dark" : "light";
    }
    return theme.value;
  });

  function apply(): void {
    if (typeof document === "undefined") return;
    document.documentElement.dataset.theme = resolvedTheme.value;
    document.documentElement.dataset.effects = effects.value;
  }

  // 立即应用 + watch 持续同步
  apply();
  watch([theme, effects], apply);

  // auto 模式下监听系统主题变化
  if (prefersDark && typeof prefersDark.addEventListener === "function") {
    const onChange = (): void => {
      if (theme.value === "auto") apply();
    };
    prefersDark.addEventListener("change", onChange);
    onScopeDispose(() => prefersDark.removeEventListener("change", onChange));
  }

  return { resolvedTheme, apply };
}
