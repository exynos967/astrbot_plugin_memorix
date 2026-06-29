import { defineStore } from "pinia";
import { ref } from "vue";
import {
  runAggregate,
  runEntity,
  runRelation,
  runSearch,
  runTime,
  type QueryMode,
  type QueryResult,
} from "@/services/queryApi";
import { queryEpisodes } from "@/services/episodeApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { useLogsStore } from "@/stores/logs";
import { useDashboardStore } from "@/stores/dashboard";

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err ?? "未知错误");
}

/**
 * Query store：统一查询中心。
 *
 * - 修复 H4：runQuery 持单调请求序号，过期请求结果丢弃，避免快速切换模式/连续点查询错位。
 * - 修复 H6：relation 模式 legacy 仅发 {subject,predicate,object} 完全忽略顶部 query-text；
 *   新实现当 subject 为空时回退用 query-text 作 subject，使 query-text 在 relation 模式生效。
 * - scope：/v1/query/* 经 bridge `_scope` 路由，统一传 graph.effectiveScope()。
 * - 查询成功后联动刷新 dashboard status（与 legacy runQuery 末尾 loadDashboardStatus 一致）。
 * - 错误显式进 useAppStore 总线，不静默。
 */
export const useQueryStore = defineStore("query", () => {
  const mode = ref<QueryMode>("aggregate");
  const query = ref("");
  const topk = ref(10);
  const timeFrom = ref("");
  const timeTo = ref("");
  const person = ref("");
  const source = ref("");
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

  function setMode(next: QueryMode): void {
    mode.value = next;
  }

  async function runQuery(): Promise<void> {
    // relation 模式不强制 query-text（可用三元组查询）
    if (mode.value !== "relation" && !query.value.trim()) {
      app.pushError("请输入查询内容", "runQuery");
      return;
    }
    seq += 1;
    const cur = seq;
    loading.value = true;
    const scope = graph.effectiveScope();
    const topK = Number(topk.value) || 10;
    const tf = timeFrom.value.trim() || null;
    const tt = timeTo.value.trim() || null;
    const pn = person.value.trim() || null;
    const sr = source.value.trim() || null;
    try {
      let data: QueryResult;
      switch (mode.value) {
        case "search":
          data = await runSearch({ query: query.value.trim(), top_k: topK }, scope);
          break;
        case "time":
          data = await runTime(
            { query: query.value.trim(), top_k: topK, time_from: tf, time_to: tt, person: pn, source: sr },
            scope,
          );
          break;
        case "episode":
          data = (await queryEpisodes(
            {
              query: query.value.trim(),
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
          data = await runEntity({ entity_name: query.value.trim() }, scope);
          break;
        case "relation": {
          // 修 H6：subject 为空时回退用 query-text，使顶部 query-text 在 relation 模式生效
          const subject = relationSubject.value.trim() || query.value.trim();
          data = await runRelation(
            { subject, predicate: relationPredicate.value.trim(), object: relationObject.value.trim() },
            scope,
          );
          break;
        }
        case "aggregate":
        default:
          data = await runAggregate(
            { query: query.value.trim(), top_k: topK, time_from: tf, time_to: tt, person: pn, source: sr, mix: true, mix_top_k: topK },
            scope,
          );
          break;
      }
      if (cur !== seq) return; // 旧请求丢弃（修 H4 错位）
      result.value = data;
      meta.value = `${mode.value} · ${data.count ?? data.results?.length ?? data.relations?.length ?? "-"} 条结果`;
      logs.log(`查询[${mode.value}]：${query.value.trim() || relationSubject.value.trim()}`, "info");
      // 联动刷新 dashboard（与 legacy 一致），失败不阻塞查询展示
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

  return {
    mode,
    query,
    topk,
    timeFrom,
    timeTo,
    person,
    source,
    relationSubject,
    relationPredicate,
    relationObject,
    result,
    meta,
    loading,
    setMode,
    runQuery,
  };
});
