import { defineStore } from "pinia";
import { ref } from "vue";
import type { ScopeOption } from "@/services/configApi";

/**
 * 图谱 store。**currentScope 是全局唯一 scope source（修 C5）**。
 *
 * P2 只放 scope 相关字段：currentScope（用户在下拉选中的 scope，""=自动/最近）、
 * resolvedScope（/api/scopes 返回的解析后默认 scope，即 scope_resolver() 结果）、
 * scopeOptions（可选 scope 列表）。
 *
 * 其余图谱字段（network/nodes/edges/zoom/simulation/layoutSnapshot/sourceFocus 等）
 * 留 P8 GraphView 阶段补全（YAGNI，不预建）。
 *
 * C5 根因：legacy loadStats 用全局 scope、loadGraph 用下拉 scope，两者写同一 metric DOM。
 * 新实现所有 scoped 请求（stats / dashboard status / graph）统一读 effectiveScope()。
 */
export const useGraphStore = defineStore("graph", () => {
  /** 用户选中的 scope；"" 表示自动/最近（用解析的默认 scope）。 */
  const currentScope = ref("");
  /** /api/scopes.current：解析后的默认 scope（scope_resolver() = known_scopes[-1]）。 */
  const resolvedScope = ref("");
  /** 可选 scope 列表（来自 /api/scopes）。 */
  const scopeOptions = ref<ScopeOption[]>([]);

  function setScope(scope: string): void {
    currentScope.value = String(scope || "");
  }

  function setResolvedScope(scope: string): void {
    resolvedScope.value = String(scope || "");
  }

  function setScopeOptions(options: ScopeOption[]): void {
    scopeOptions.value = Array.isArray(options) ? options : [];
  }

  /** 实际用于请求的 scope：选中非空则用选中，否则回退到解析的默认 scope。 */
  function effectiveScope(): string {
    return currentScope.value || resolvedScope.value || "";
  }

  return {
    currentScope,
    resolvedScope,
    scopeOptions,
    setScope,
    setResolvedScope,
    setScopeOptions,
    effectiveScope,
  };
});
