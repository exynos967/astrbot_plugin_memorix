import { computed } from "vue";
import { storeToRefs } from "pinia";
import { fetchScopes } from "@/services/configApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";

/**
 * Scope 编排 composable：全局加载可选 scope 列表 + 切换当前作用域 + 计算可读作用域文案。
 * 统一过滤掉内部占位符 "default"，与 useGraphStore.loadScopes 保持完全一致。
 */
export function useScope() {
  const graph = useGraphStore();
  const app = useAppStore();
  const logs = useLogsStore();
  const { currentScope, resolvedScope, scopeOptions } = storeToRefs(graph);

  const scopeText = computed<string>(() => {
    const active = currentScope.value || resolvedScope.value || "";
    if (!active) return "自动（最近活跃）";
    const matched = scopeOptions.value.find((s) => s.value === active);
    const label = matched?.label || active;
    return currentScope.value ? label : `自动（${label}）`;
  });

  async function loadScopes(): Promise<void> {
    try {
      const data = await fetchScopes();
      const scopes = (data.scopes || []).filter((s) => s.value !== "default");
      const current = data.current && data.current !== "default" ? data.current : "";
      graph.setResolvedScope(current);
      graph.setScopeOptions(scopes);
    } catch (err) {
      app.pushError(errText(err), "加载作用域");
    }
  }

  function changeScope(scope: string): void {
    const next = String(scope || "");
    graph.setScope(next);
    logs.log(next ? `已切换作用域：${next}` : "已切回自动作用域", "info", "作用域");
  }

  return {
    currentScope,
    resolvedScope,
    scopeOptions,
    scopeText,
    loadScopes,
    changeScope,
  };
}
