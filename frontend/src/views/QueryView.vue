<script setup lang="ts">
// 统一检索中心：7 模式检索（智能路由 auto / 多路聚合 aggregate / 语义 search / 时序 time / 情景 episode / 关系 relation / 实体 entity）。
// 采用扁平 1px 细线行列表与分区（<=2 层盒嵌套），消除多层盒套盒视觉噪音。
import { computed, onMounted, ref } from "vue";
import { storeToRefs } from "pinia";
import { useQueryStore } from "@/stores/query";
import { useGraphStore } from "@/stores/graph";
import { useScope } from "@/composables/useScope";
import CandidateInput from "@/components/common/CandidateInput.vue";
import {
  buildEntitySections,
  buildQueryItems,
  deriveAggregateBranchChips,
  QUERY_MODE_LABELS,
  routeReasonLabel,
  truncateBody,
} from "@/utils/queryText";
import type { QueryMode } from "@/services/queryApi";
import type { CandidateItem } from "@/stores/candidate";
import { fetchPersonRegistry } from "@/services/personApi";
import { personCandidateValues } from "@/utils/personText";

const store = useQueryStore();
const graph = useGraphStore();
const { loadScopes } = useScope();

const {
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
} = storeToRefs(store);

const MODES: Array<{ key: QueryMode; label: string }> = [
  { key: "auto", label: QUERY_MODE_LABELS.auto },
  { key: "aggregate", label: QUERY_MODE_LABELS.aggregate },
  { key: "search", label: QUERY_MODE_LABELS.search },
  { key: "time", label: QUERY_MODE_LABELS.time },
  { key: "episode", label: QUERY_MODE_LABELS.episode },
  { key: "relation", label: QUERY_MODE_LABELS.relation },
  { key: "entity", label: QUERY_MODE_LABELS.entity },
];

const showTopK = computed(() =>
  ["auto", "aggregate", "search", "time", "episode"].includes(mode.value),
);
const showSourceFilter = computed(() =>
  ["auto", "aggregate", "search", "time", "episode"].includes(mode.value),
);
const showStrictSource = computed(() => ["auto", "search"].includes(mode.value));
const showTimeAndPerson = computed(() =>
  ["aggregate", "time", "episode"].includes(mode.value),
);
const showRelationTriples = computed(() => mode.value === "relation");
const hasFilterSection = computed(
  () => showTopK.value || showSourceFilter.value || showTimeAndPerson.value || showRelationTriples.value,
);

const queryInputLabel = computed(() => {
  if (mode.value === "entity") return "目标实体名称";
  if (mode.value === "relation") return "快捷主体或关键词（可选）";
  if (mode.value === "auto") return "自然语言检索（自动解析时间与语义）";
  return "检索内容";
});

const queryPlaceholder = computed(() => {
  if (mode.value === "entity") return "输入实体名称（如：爱丽丝），查看关系、段落与邻接实体";
  if (mode.value === "relation") return "可留空并在下方填写三元组，或直接输入主体名称";
  if (mode.value === "auto") return "例如：上周爱丽丝和鲍勃聊了什么计划？";
  if (showTimeAndPerson.value) return "输入关键词，或留空仅按人物 / 来源 / 时间区间筛选";
  return "输入要检索的关键词或问题";
});

function graphNodeSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  return graph.nodeLabels
    .filter((v) => {
      if (!q) return true;
      const t = v.toLowerCase();
      return t === q || t.startsWith(q) || t.includes(q);
    })
    .slice(0, 24)
    .map((value) => ({ value, kind: "实体" }));
}

function predicateSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  return graph.predicateLabels
    .filter((v) => !q || v.toLowerCase().includes(q))
    .slice(0, 24)
    .map((value) => ({ value, kind: "谓词" }));
}

async function personSource(kw: string): Promise<CandidateItem[]> {
  try {
    const data = await fetchPersonRegistry(kw.trim(), 1, 30, graph.effectiveScope());
    return personCandidateValues(data.items).map((value) => ({ value, kind: "人物" }));
  } catch {
    return [];
  }
}

async function sourceSource(kw: string): Promise<CandidateItem[]> {
  const names = await store.getSourceCandidates(kw);
  return names.map((value) => ({ value, kind: "来源" }));
}

function onEnterKey(e: KeyboardEvent): void {
  if (e.key !== "Enter") return;
  e.preventDefault();
  if (!loading.value) void store.runQuery();
}

function queryNeighborEntity(neighborName: string): void {
  if (!neighborName) return;
  store.setMode("entity");
  query.value = neighborName;
  void store.runQuery();
}

const items = computed(() => (result.value ? buildQueryItems(result.value) : []));
const entitySections = computed(() =>
  mode.value === "entity" ? buildEntitySections(result.value) : null,
);
const entityHasAnyData = computed(() => {
  const s = entitySections.value;
  if (!s) return false;
  return s.relations.length > 0 || s.paragraphs.length > 0 || s.neighbors.length > 0;
});

const branchChips = computed(() =>
  mode.value === "aggregate" ? deriveAggregateBranchChips(result.value) : [],
);

const aggregateErrors = computed(() => {
  if (mode.value !== "aggregate" || !Array.isArray(result.value?.errors)) return [];
  return result.value.errors.filter((e) => e && (e.error || e.branch));
});

const autoRouteChips = computed(() => {
  if (mode.value !== "auto" || !result.value) return [];
  const pills: string[] = [];
  if (result.value.query_type) {
    const typeMap: Record<string, string> = {
      search: "路由至：语义检索",
      time: "路由至：时序检索",
      hybrid: "路由至：时间 + 语义混合",
    };
    pills.push(typeMap[result.value.query_type] || `通道：${result.value.query_type}`);
  }
  const reason = routeReasonLabel(result.value.route_reason);
  if (reason) pills.push(reason);
  if (result.value.matched_time) {
    pills.push(`时间词：${result.value.matched_time}`);
  }
  if (result.value.time_from || result.value.time_to) {
    pills.push(`时间窗：${result.value.time_from || "不限"} ~ ${result.value.time_to || "不限"}`);
  }
  return pills;
});

const showRaw = ref(false);
const rawJson = computed(() => (result.value ? JSON.stringify(result.value, null, 2) : ""));

onMounted(async () => {
  await loadScopes();
  if (!graph.nodeLabels.length && !graph.loading) {
    void graph.loadGraph();
  }
});
</script>

<template>
  <section class="view-query">
    <div class="band">
      <div class="panel-title">
        <h2>检索模式与条件</h2>
        <div class="segmented mode-tabs" role="tablist" aria-label="检索模式">
          <button
            v-for="m in MODES"
            :key="m.key"
            type="button"
            role="tab"
            :aria-selected="mode === m.key"
            :class="{ active: mode === m.key }"
            @click="store.setMode(m.key)"
          >
            {{ m.label }}
          </button>
        </div>
      </div>

      <div class="toolbar">
        <CandidateInput
          v-model="query"
          :source="graphNodeSource"
          :debounce-ms="80"
          :flex="2.4"
          :label="queryInputLabel"
          :placeholder="queryPlaceholder"
          @keydown="onEnterKey"
        />
        <button
          class="btn primary"
          type="button"
          :disabled="loading"
          @click="store.runQuery()"
        >
          {{ loading ? "检索中…" : "执行检索" }}
        </button>
        <button
          class="btn"
          type="button"
          :disabled="loading"
          @click="store.resetFilters()"
        >
          清空条件
        </button>
      </div>

      <!-- 按当前模式动态展示的筛选参数区 -->
      <div v-if="hasFilterSection" class="filter-panel">
        <div v-if="showTopK || showSourceFilter || showTimeAndPerson" class="toolbar">
          <div v-if="showTopK" class="field topk-field">
            <label for="query-topk-input">返回条数</label>
            <input
              id="query-topk-input"
              v-model.number="topk"
              class="input tabular"
              type="number"
              min="1"
              max="100"
              @keyup.enter="store.runQuery()"
            />
          </div>

          <CandidateInput
            v-if="showSourceFilter"
            v-model="source"
            :source="sourceSource"
            :debounce-ms="160"
            label="限定来源"
            placeholder="可选：记忆来源名称"
            :flex="1.4"
            @keydown="onEnterKey"
          />

          <label v-if="showStrictSource" class="field strict-source-field">
            <span>来源约束</span>
            <select v-model="strictSource" class="select">
              <option :value="false">优先加权</option>
              <option :value="true">严格过滤</option>
            </select>
          </label>

          <CandidateInput
            v-if="showTimeAndPerson"
            v-model="person"
            :source="personSource"
            :debounce-ms="180"
            label="关联人物"
            placeholder="可选：人物名称或 ID"
            :flex="1.3"
            @keydown="onEnterKey"
          />

          <div v-if="showTimeAndPerson" class="field time-field">
            <label for="query-time-from">起始时间</label>
            <input
              id="query-time-from"
              v-model="timeFrom"
              class="input tabular"
              placeholder="如 2026-05-01"
              @keyup.enter="store.runQuery()"
            />
          </div>

          <div v-if="showTimeAndPerson" class="field time-field">
            <label for="query-time-to">结束时间</label>
            <input
              id="query-time-to"
              v-model="timeTo"
              class="input tabular"
              placeholder="如 2026-05-31"
              @keyup.enter="store.runQuery()"
            />
          </div>
        </div>

        <div v-if="showRelationTriples" class="grid-3 relation-triples">
          <CandidateInput
            v-model="relationSubject"
            :source="graphNodeSource"
            :debounce-ms="60"
            label="主体实体 (Subject)"
            placeholder="例如：爱丽丝"
            @keydown="onEnterKey"
          />
          <CandidateInput
            v-model="relationPredicate"
            :source="predicateSource"
            :debounce-ms="60"
            label="关系谓词 (Predicate)"
            placeholder="例如：喜欢 / 负责"
            @keydown="onEnterKey"
          />
          <CandidateInput
            v-model="relationObject"
            :source="graphNodeSource"
            :debounce-ms="60"
            label="客体实体 (Object)"
            placeholder="例如：Memorix"
            @keydown="onEnterKey"
          />
        </div>
      </div>
    </div>

    <div class="band">
      <div class="panel-title">
        <div class="result-title-left">
          <h2>检索结果</h2>
          <span class="section-label tabular">{{ meta }}</span>
        </div>
        <button
          v-if="result"
          class="btn sm"
          type="button"
          @click="showRaw = !showRaw"
        >
          {{ showRaw ? "隐藏原始 JSON" : "查看原始 JSON" }}
        </button>
      </div>

      <!-- 智能路由 (auto) 诊断胶囊 -->
      <div v-if="autoRouteChips.length" class="diagnostics-bar">
        <span v-for="chip in autoRouteChips" :key="chip" class="tag ok">{{ chip }}</span>
      </div>

      <!-- 多路聚合 (aggregate) 三路分支诊断与错误提示 -->
      <div v-if="branchChips.length" class="diagnostics-bar">
        <span
          v-for="b in branchChips"
          :key="b.key"
          class="tag tabular"
          :class="b.tone"
        >
          {{ b.label }}：{{ b.detail }}
        </span>
      </div>

      <div v-if="aggregateErrors.length" class="branch-error-banner" role="alert">
        <strong>部分子路检索异常：</strong>
        <span v-for="(err, idx) in aggregateErrors" :key="idx">
          [{{ err.branch || "未知分支" }}] {{ err.error || "执行失败" }}
        </span>
      </div>

      <div v-if="loading" class="empty">正在检索记忆记录…</div>

      <!-- 实体模式：扁平 1px 细线三大分区（邻接实体 / 关联关系 / 提及段落） -->
      <template v-else-if="mode === 'entity' && result">
        <div v-if="!entityHasAnyData" class="empty">
          未找到实体「{{ entitySections?.entityName || query }}」的关联关系、段落或邻接节点
        </div>
        <div v-else class="entity-sections">
          <!-- 1. 邻接实体 -->
          <div class="entity-section">
            <div class="entity-section-head">
              <h3>邻接实体</h3>
              <span class="tag tabular">{{ entitySections?.neighbors.length ?? 0 }} 个</span>
            </div>
            <div v-if="!entitySections?.neighbors.length" class="empty">暂无邻接实体</div>
            <div v-else class="tags">
              <button
                v-for="nb in entitySections.neighbors"
                :key="nb"
                type="button"
                class="neighbor-pill"
                :title="`检索实体 ${nb}`"
                @click="queryNeighborEntity(nb)"
              >
                {{ nb }} ›
              </button>
            </div>
          </div>

          <!-- 2. 关联关系 -->
          <div class="entity-section">
            <div class="entity-section-head">
              <h3>关联关系三元组</h3>
              <span class="tag tabular">{{ entitySections?.relations.length ?? 0 }} 条</span>
            </div>
            <div v-if="!entitySections?.relations.length" class="empty">暂无关联图谱关系</div>
            <div v-else class="rows">
              <article
                v-for="(rel, ri) in entitySections.relations"
                :key="ri"
                class="query-row"
              >
                <h4 class="query-row-title tabular">{{ ri + 1 }}. {{ rel.title }}</h4>
                <p v-if="rel.body" class="query-row-body">{{ truncateBody(rel.body) }}</p>
                <div v-if="rel.badges.length" class="tags">
                  <span
                    v-for="(bg, bi) in rel.badges"
                    :key="bi"
                    class="tag"
                    :class="[bg.tone || '', { mono: bg.mono }]"
                  >
                    {{ bg.text }}
                  </span>
                </div>
              </article>
            </div>
          </div>

          <!-- 3. 提及段落 -->
          <div class="entity-section">
            <div class="entity-section-head">
              <h3>提及原文段落</h3>
              <span class="tag tabular">{{ entitySections?.paragraphs.length ?? 0 }} 条</span>
            </div>
            <div v-if="!entitySections?.paragraphs.length" class="empty">暂无提及该实体的记忆段落</div>
            <div v-else class="rows">
              <article
                v-for="(para, pi) in entitySections.paragraphs"
                :key="pi"
                class="query-row"
              >
                <h4 class="query-row-title tabular">{{ pi + 1 }}. {{ para.title }}</h4>
                <p v-if="para.body" class="query-row-body">{{ truncateBody(para.body) }}</p>
                <div v-if="para.badges.length" class="tags">
                  <span
                    v-for="(bg, bi) in para.badges"
                    :key="bi"
                    class="tag"
                    :class="[bg.tone || '', { mono: bg.mono }]"
                  >
                    {{ bg.text }}
                  </span>
                </div>
              </article>
            </div>
          </div>
        </div>
      </template>

      <!-- 其他 6 种模式通用扁平细线结果列表 -->
      <div v-else-if="!items.length" class="empty">
        {{ result ? "未检索到符合条件的记忆记录" : "输入检索条件后按回车或点击「执行检索」" }}
      </div>
      <div v-else class="rows result-rows">
        <article v-for="(item, i) in items" :key="i" class="query-row">
          <h3 class="query-row-title tabular">{{ i + 1 }}. {{ item.title }}</h3>
          <p v-if="item.body" class="query-row-body">{{ truncateBody(item.body) }}</p>
          <div v-if="item.badges.length" class="tags">
            <span
              v-for="(bg, ti) in item.badges"
              :key="ti"
              class="tag"
              :class="[bg.tone || '', { mono: bg.mono }]"
            >
              {{ bg.text }}
            </span>
          </div>
        </article>
      </div>

      <pre v-if="showRaw && rawJson" class="json raw-json">{{ rawJson }}</pre>
    </div>
  </section>
</template>

<style scoped>
.view-query {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.view-query > .band {
  margin-top: 0;
}

.mode-tabs {
  flex-wrap: wrap;
}

.filter-panel {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.topk-field {
  max-width: 112px;
}

.strict-source-field {
  max-width: 150px;
}

.time-field {
  flex: 1 1 132px;
}

.relation-triples {
  margin-top: 8px;
}

@media (max-width: 640px) {
  .topk-field,
  .strict-source-field {
    max-width: none;
  }
}

.result-title-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.diagnostics-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.branch-error-banner {
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--hairline);
  background: var(--red-soft);
  color: var(--red);
  font-size: 13px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.entity-sections {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.entity-section {
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
}

.entity-section:first-child {
  padding-top: 4px;
  border-top: 0;
}

.entity-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.entity-section-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  color: var(--ink);
}

.neighbor-pill {
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--hairline-strong);
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--m-out) var(--ease), border-color var(--m-out) var(--ease);
}

.neighbor-pill:hover {
  background: var(--surface-strong);
}

.neighbor-pill:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.result-rows {
  border-top: 1px solid var(--hairline);
}

.query-row {
  padding: 12px 0;
}

.query-row-title {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.query-row-body {
  margin: 6px 0 0;
  font-size: 13.5px;
  line-height: 1.58;
  color: var(--muted);
  white-space: pre-wrap;
  word-break: break-word;
}

.raw-json {
  margin-top: 12px;
}
</style>
