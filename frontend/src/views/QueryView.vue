<script setup lang="ts">
// 统一查询中心：六模式（智能聚合/语义/时间/情景/关系/实体）查询。
// 从 legacy view-query（index.html 行 2158-2201, 4239-4330）迁移。
//
// 修复点：
// - H6：relation 模式合并 query-text（store.runQuery 内：subject 为空时回退 query-text）。
// - H4：快速切换模式/连续查询的错位由 store 序号守卫处理。
// - 候选菜单复用 useCandidateMenu（封装在 CandidateInput）：实体/人物/来源/谓词输入框各接候选源。
//   实体候选来自 graph.nodeLabels（P8 loadGraph 填充）；谓词候选 P8 填充，当前为空。
// - 消除 window.showRawDetail 等，结果以 Vue v-for 渲染，原始数据展开式展示。
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import { useQueryStore } from "@/stores/query";
import { useGraphStore } from "@/stores/graph";
import CandidateInput from "@/components/common/CandidateInput.vue";
import { buildQueryItems, truncateBody } from "@/utils/queryText";
import type { CandidateItem } from "@/stores/candidate";
import { fetchPersonRegistry } from "@/services/personApi";
import { personCandidateValues } from "@/utils/personText";
import { fetchSourceList } from "@/services/sourceApi";

const store = useQueryStore();
const graph = useGraphStore();
const {
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
} = storeToRefs(store);

const MODES: { key: typeof mode.value; label: string }[] = [
  { key: "aggregate", label: "智能聚合" },
  { key: "search", label: "语义" },
  { key: "time", label: "时间" },
  { key: "episode", label: "情景" },
  { key: "relation", label: "关系" },
  { key: "entity", label: "实体" },
];

// 实体候选：graph.nodeLabels 按关键词前缀/包含过滤（同步）。P8 loadGraph 后填充。
function graphNodeSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  const labels = graph.nodeLabels;
  return labels
    .filter((v) => {
      if (!q) return true;
      const t = v.toLowerCase();
      return t === q || t.startsWith(q) || t.includes(q);
    })
    .slice(0, 10)
    .map((value) => ({ value, kind: "实体" }));
}

// 人物候选：拉 registry 摊平（异步，180ms 防抖）。
async function personSource(kw: string): Promise<CandidateItem[]> {
  const data = await fetchPersonRegistry(kw.trim(), 1, 30);
  return data.items.flatMap(personCandidateValues).map((value) => ({ value, kind: "人物" }));
}

// 来源候选：拉 source summary 摊平 source 名（异步，180ms 防抖）。
async function sourceSource(kw: string): Promise<CandidateItem[]> {
  const data = await fetchSourceList({});
  const q = kw.trim().toLowerCase();
  return (data.sources || [])
    .map((s) => (typeof s === "string" ? s : s.source || ""))
    .filter(Boolean)
    .filter((v) => !q || v.toLowerCase().includes(q))
    .slice(0, 10)
    .map((value) => ({ value, kind: "来源" }));
}

// 谓词候选：P8 由 graph edges 填充，当前为空（YAGNI，不预建占位数据）。
function predicateSource(): CandidateItem[] {
  return [];
}

const items = computed(() => (result.value ? buildQueryItems(result.value) : []));

// 原始 JSON 展开切换（替代 legacy showRawDetail）。
const showRaw = ref(false);
const rawJson = computed(() => (result.value ? JSON.stringify(result.value, null, 2) : ""));
</script>

<template>
  <section class="view-query">
    <div class="band" style="margin-top: 0">
      <div class="panel-title">
        <h2>统一查询</h2>
        <div class="segmented">
          <button
            v-for="m in MODES"
            :key="m.key"
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
          :debounce-ms="0"
          :flex="2"
          label="查询内容"
          placeholder="例如：Alice 最近和 Bob 讨论了什么"
        />
        <button class="btn primary" :disabled="loading" @click="store.runQuery()">执行查询</button>
      </div>
      <details class="advanced-panel" :open="mode === 'relation'">
        <summary>高级筛选</summary>
        <div class="toolbar" style="margin-top: 10px">
          <div class="field">
            <label>结果数</label>
            <input v-model.number="topk" class="input" type="number" min="1" max="50" />
          </div>
          <CandidateInput v-model="timeFrom" :source="graphNodeSource" :debounce-ms="0" label="起始时间" placeholder="2026-05-01" />
          <CandidateInput v-model="timeTo" :source="graphNodeSource" :debounce-ms="0" label="结束时间" placeholder="2026-05-21" />
          <CandidateInput v-model="person" :source="personSource" :debounce-ms="180" label="人物" placeholder="person" />
          <CandidateInput v-model="source" :source="sourceSource" :debounce-ms="180" label="来源" placeholder="来源名称" />
        </div>
        <div v-if="mode === 'relation'" class="grid-3" style="margin-top: 12px">
          <CandidateInput v-model="relationSubject" :source="graphNodeSource" :debounce-ms="0" label="主体" placeholder="Alice" />
          <CandidateInput v-model="relationPredicate" :source="predicateSource" :debounce-ms="0" label="关系" placeholder="负责" />
          <CandidateInput v-model="relationObject" :source="graphNodeSource" :debounce-ms="0" label="客体" placeholder="A_Memorix WebUI" />
        </div>
      </details>
    </div>

    <div class="band">
      <div class="panel-title">
        <h2>查询结果</h2>
        <span class="section-label">{{ meta }}</span>
        <button class="btn icon" :title="showRaw ? '收起原始' : '展开原始'" @click="showRaw = !showRaw">⋯</button>
      </div>
      <div v-if="loading" class="empty">查询中</div>
      <div v-else-if="!items.length" class="empty">没有结果</div>
      <div v-else class="result-list">
        <div v-for="(item, i) in items" :key="i" class="result">
          <div class="result-head">
            <h3>{{ i + 1 }}. {{ item.title }}</h3>
          </div>
          <p v-if="item.body">{{ truncateBody(item.body) }}</p>
          <div v-if="item.tags.length" class="tags">
            <span v-for="(tag, ti) in item.tags" :key="ti" class="tag">{{ tag }}</span>
          </div>
        </div>
      </div>
      <pre v-if="showRaw && rawJson" class="json">{{ rawJson }}</pre>
    </div>
  </section>
</template>

<style scoped>
.view-query {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.view-query .result p {
  margin: 4px 0;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
