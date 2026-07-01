import { fetchScopes } from "@/services/configApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";


/**
 * Scope 编排 composable：加载可选 scope 列表 + 切换 scope。
 *
 * - loadScopes：拉 /api/scopes，写入 graph.resolvedScope / scopeOptions。
 *   resolvedScope 即 scope_resolver() 解析的默认 scope（= known_scopes[-1]），
 *   用于 effectiveScope 回退——这正是修 C5 的关键：dashboard 不再命中空 "default"。
 *
 * - changeScope：写入 graph.currentScope（全局唯一 source）。
 *   dashboard store watch currentScope → 自动联动刷新 stats + status（无需调用方手动刷新）。
 *
 * P2 仅 dashboard 在挂载时调 loadScopes；scope 下拉 UI 在 P8 GraphView 接入 changeScope。
 */
export function useScope() {
  const graph = useGraphStore();
  const app = useAppStore();
  const logs = useLogsStore();

  async function loadScopes(): Promise<void> {
    try {
      const data = await fetchScopes();
      graph.setResolvedScope(data.current);
      graph.setScopeOptions(data.scopes);
    } catch (err) {
      // 失败不阻塞 dashboard：effectiveScope 回退 ""，由 bridge resolver 兜底。
      app.pushError(errText(err), "loadScopes");
    }
  }

  function changeScope(scope: string): void {
    const next = String(scope || "");
    graph.setScope(next);
    logs.log(next ? `已选择群：${next}` : "已切回自动 scope", "info");
  }

  return { loadScopes, changeScope };
}
