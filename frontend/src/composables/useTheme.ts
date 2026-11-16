import { computed, onScopeDispose, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";

function parseHostDark(ctx: AstrBotPluginPageContext | null | undefined): boolean | null {
  if (!ctx || typeof ctx !== "object") return null;
  if (typeof ctx.isDark === "boolean") return ctx.isDark;
  if (ctx.theme === "dark") return true;
  if (ctx.theme === "light") return false;
  return null;
}

/**
 * 主题/特效同步：
 * 1. "auto" 模式优先跟随 AstrBot 宿主桥接上下文（window.AstrBotPluginPage.onContext / getContext），
 *    宿主未提供时回退系统 prefers-color-scheme。
 * 2. 用户显式选择 "light" 或 "dark" 时，在收到宿主 context 消息后立即重新断言 documentElement.dataset.theme，
 *    防止宿主桥接 SDK 覆写用户的显式主题偏好。
 */
export function useTheme() {
  const store = useUiStore();
  const { theme, effects } = storeToRefs(store);

  const hostDark = ref<boolean | null>(null);
  const systemDark = ref<boolean>(false);

  const prefersDark =
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;

  if (prefersDark) {
    systemDark.value = prefersDark.matches;
  }

  const resolvedTheme = computed<"light" | "dark">(() => {
    if (theme.value === "auto") {
      if (hostDark.value !== null) {
        return hostDark.value ? "dark" : "light";
      }
      return systemDark.value ? "dark" : "light";
    }
    return theme.value;
  });

  function apply(): void {
    if (typeof document === "undefined") return;
    document.documentElement.dataset.theme = resolvedTheme.value;
    document.documentElement.dataset.effects = effects.value;
    document.documentElement.style.colorScheme = resolvedTheme.value;
  }

  function handleHostContext(ctx: AstrBotPluginPageContext | null | undefined): void {
    const parsed = parseHostDark(ctx);
    if (parsed !== null) {
      hostDark.value = parsed;
    }
    apply();
    // 队列微任务再次断言，确保晚于 page-bridge-sdk 自身的 dataset.theme 写入
    queueMicrotask(apply);
  }

  apply();
  watch([resolvedTheme, effects], apply);

  if (typeof window !== "undefined") {
    const bridge = window.AstrBotPluginPage;
    if (bridge?.getContext) {
      try {
        const maybePromise = bridge.getContext();
        if (maybePromise && typeof (maybePromise as Promise<AstrBotPluginPageContext>).then === "function") {
          void (maybePromise as Promise<AstrBotPluginPageContext>)
            .then((ctx) => handleHostContext(ctx))
            .catch(() => undefined);
        } else {
          handleHostContext(maybePromise as AstrBotPluginPageContext);
        }
      } catch {
        // 忽略旧版桥接未实现异常
      }
    }

    if (bridge?.onContext) {
      try {
        const off = bridge.onContext((ctx) => handleHostContext(ctx));
        if (typeof off === "function") {
          onScopeDispose(() => off());
        }
      } catch {
        // 忽略桥接监听异常
      }
    }
  }

  if (prefersDark && typeof prefersDark.addEventListener === "function") {
    const onChange = (ev: MediaQueryListEvent): void => {
      systemDark.value = ev.matches;
      if (theme.value === "auto") apply();
    };
    prefersDark.addEventListener("change", onChange);
    onScopeDispose(() => prefersDark.removeEventListener("change", onChange));
  }

  return { resolvedTheme, apply };
}
