import { defineStore } from "pinia";
import { ref, watch } from "vue";
import {
  runAggregate,
  runAuto,
  runEntity,
  runRelation,
  runSearch,
  runTime,
  type QueryMode,
  type QueryResult,
} from "@/services/queryApi";
import { queryEpisodes } from "@/services/episodeApi";
import { fetchSourceList } from "@/services/sourceApi";
import { sourceNameOf } from "@/utils/sourceText";
import { queryMetaLabel, queryModeLabel } from "@/utils/queryText";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { useDashboardStore } from "@/stores/dashboard";
import { errText } from "@/utils/error";

/**
 * Query store：7 模式统一检索中心（auto / aggregate / search / time / episode / relation / entity）。
 * - 支持作用域级真实来源缓存补全（getSourceCandidates）。
 * - 允许 time / episode / aggregate 在设置了人物/来源/时间范围时免关键词筛选查询。
 * - 监听 graph.effectiveScope()，切换作用域时清除旧作用域残留结果。
 */
export const useQueryStore = defineStore("query", () => {
  const mode = ref<QueryMode>("auto");
  const query = ref("");
  const topk = ref(10);
  const timeFrom = ref("");
  const timeTo = ref("");
  const person = ref("");
  const source = ref("");
  const strictSource = ref(false);
  const relationSubject = ref("");
  const relationPredicate = ref("");
  const relationObject = ref("");

  const result = ref<QueryResult | null>(null);
  const meta = ref("等待查询");
  const loading = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();
  const dashboard = useDashboardStore();

  let seq = 0;

  // 按作用域缓存真实来源列表，避免每次按键重复打后端
  let cachedSourceScope: string | null = null;
  let cachedSources: string[] = [];
  let pendingSourcePromise: Promise<string[]> | null = null;

  function invalidateSourceCache(): void {
    cachedSourceScope = null;
    cachedSources = [];
    pendingSourcePromise = null;
  }

  async function getSourceCandidates(kw: string): Promise<string[]> {
    const scope = graph.effectiveScope();
    let list = cachedSources;
    if (cachedSourceScope !== scope || cachedSources.length === 0) {
      if (!pendingSourcePromise || cachedSourceScope !== scope) {
        cachedSourceScope = scope;
        pendingSourcePromise = fetchSourceList({}, scope)
          .then((res) => {
            const names = Array.from(
              new Set((res.sources || []).map((it) => sourceNameOf(it)).filter(Boolean)),
            ).sort((a, b) => a.localeCompare(b, "zh-CN"));
            if (cachedSourceScope === scope) {
              cachedSources = names;
            }
            return names;
          })
          .catch(() => [])
          .finally(() => {
            pendingSourcePromise = null;
          });
      }
      list = await pendingSourcePromise;
    }
    const needle = kw.trim().toLowerCase();
    if (!needle) return list.slice(0, 40);
    return list.filter((s) => s.toLowerCase().includes(needle)).slice(0, 40);
  }

  function setMode(next: QueryMode): void {
    if (mode.value === next) return;
    mode.value = next;
    result.value = null;
    meta.value = "等待查询";
  }

  function resetFilters(): void {
    query.value = "";
    topk.value = 10;
    timeFrom.value = "";
    timeTo.value = "";
    person.value = "";
    source.value = "";
    strictSource.value = false;
    relationSubject.value = "";
    relationPredicate.value = "";
    relationObject.value = "";
    result.value = null;
    meta.value = "等待查询";
  }

  async function runQuery(): Promise<void> {
    const q = query.value.trim();
    const tf = timeFrom.value.trim() || null;
    const tt = timeTo.value.trim() || null;
    const pn = person.value.trim() || null;
    const sr = source.value.trim() || null;
    const sub = relationSubject.value.trim();
    const pred = relationPredicate.value.trim();
    const obj = relationObject.value.trim();

    if (mode.value === "auto" || mode.value === "search") {
      if (!q) {
        app.pushError("请输入检索内容", "runQuery");
        return;
      }
    } else if (mode.value === "entity") {
      if (!q) {
        app.pushError("请输入要查询的实体名称", "runQuery");
        return;
      }
    } else if (mode.value === "relation") {
      if (!sub && !pred && !obj && !q) {
        app.pushError("请至少填写主体、谓词、客体或顶部检索词中的一项", "runQuery");
        return;
      }
    } else if (mode.value === "time" || mode.value === "episode" || mode.value === "aggregate") {
      if (!q && !pn && !sr && !tf && !tt) {
        app.pushError("请输入检索词，或至少设置人物 / 来源 / 时间范围中的一项筛选条件", "runQuery");
        return;
      }
    }

    seq += 1;
    const cur = seq;
    loading.value = true;
    const scope = graph.effectiveScope();
    const topK = Math.max(1, Math.min(100, Number(topk.value) || 10));

    try {
      let data: QueryResult;
      switch (mode.value) {
        case "auto":
          data = await runAuto(
            {
              query: q,
              top_k: topK,
              source: sr,
              strict_source: strictSource.value,
            },
            scope,
          );
          break;
        case "search":
          data = await runSearch(
            {
              query: q,
              top_k: topK,
              ...(sr ? { source: sr, strict_source: strictSource.value } : {}),
            },
            scope,
          );
          break;
        case "time":
          data = await runTime(
            { query: q, top_k: topK, time_from: tf, time_to: tt, person: pn, source: sr },
            scope,
          );
          break;
        case "episode":
          data = (await queryEpisodes(
            {
              query: q,
              top_k: topK,
              time_from: tf,
              time_to: tt,
              person: pn,
              source: sr,
              include_paragraphs: true,
            },
            scope,
          )) as unknown as QueryResult;
          break;
        case "entity":
          data = await runEntity({ entity_name: q }, scope);
          break;
        case "relation": {
          const effectiveSubject = sub || (!pred && !obj ? q : "");
          data = await runRelation(
            { subject: effectiveSubject, predicate: pred, object: obj },
            scope,
          );
          break;
        }
        case "aggregate":
        default:
          data = await runAggregate(
            {
              query: q,
              top_k: topK,
              time_from: tf,
              time_to: tt,
              person: pn,
              source: sr,
              mix: true,
              mix_top_k: topK,
            },
            scope,
          );
          break;
      }
      if (cur !== seq) return;
      result.value = data;
      meta.value = queryMetaLabel(mode.value, data);

      if (mode.value === "aggregate" && Array.isArray(data.errors) && data.errors.length > 0) {
        const branchText = data.errors
          .map((e) => `${e.branch || "子路"}: ${e.error || "执行异常"}`)
          .join("；");
        app.notify?.(`部分检索分支返回异常（${branchText}）`, "warn", "aggregateQuery");
      }

      const logTarget = q || sub || pred || obj || sr || pn || `${tf || ""}~${tt || ""}`;
      logs.log(`检索 [${queryModeLabel(mode.value)}]：${logTarget}`, "info");
      void dashboard.loadDashboardStatus().catch(() => undefined);
    } catch (err) {
      if (cur === seq) {
        app.pushError(errText(err), "runQuery");
        meta.value = "查询失败";
        result.value = null;
      }
    } finally {
      if (cur === seq) loading.value = false;
    }
  }

  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (next === prev) return;
      seq += 1;
      loading.value = false;
      invalidateSourceCache();
      result.value = null;
      meta.value = "已切换作用域，请重新执行检索";
    },
  );

  return {
    mode,
    query,
    topk,
    timeFrom,
    timeTo,
    person,
    source,
    strictSource,
    relationSubject,
    relationPredicate,
    relationObject,
    result,
    meta,
    loading,
    getSourceCandidates,
    setMode,
    resetFilters,
    runQuery,
  };
});
