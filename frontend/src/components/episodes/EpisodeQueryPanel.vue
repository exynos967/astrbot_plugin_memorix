<script setup lang="ts">
// Episode 查询筛选面板：支持关键词、真实来源补全、人物补全、起止时间区间与回车检索。
import { storeToRefs } from "pinia";
import { useEpisodeStore } from "@/stores/episode";
import { useGraphStore } from "@/stores/graph";
import CandidateInput from "@/components/common/CandidateInput.vue";
import type { CandidateItem } from "@/stores/candidate";
import { fetchPersonRegistry } from "@/services/personApi";
import { personCandidateValues } from "@/utils/personText";

const store = useEpisodeStore();
const graph = useGraphStore();
const { query, source, person, timeFrom, timeTo, topk, loading } = storeToRefs(store);

async function realSourceCandidates(kw: string): Promise<CandidateItem[]> {
  const list = await store.getSourceCandidates(kw);
  return list.map((value) => ({ value, kind: "来源" }));
}

async function personCandidates(kw: string): Promise<CandidateItem[]> {
  try {
    const data = await fetchPersonRegistry(kw.trim(), 1, 24, graph.effectiveScope());
    return personCandidateValues(data.items).map((value) => ({ value, kind: "人物" }));
  } catch {
    return [];
  }
}

function onEnter(): void {
  if (!loading.value) void store.loadList();
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>情景记忆检索</h2>
      <span class="section-label">关键词、来源、人物与时间区间联合筛选</span>
    </div>

    <div class="toolbar">
      <div class="field" style="flex: 2 1 180px">
        <label for="episode-query-kw">检索关键词</label>
        <input
          id="episode-query-kw"
          v-model="query"
          class="input"
          placeholder="留空可按条件列出最新情景"
          @keyup.enter="onEnter"
        />
      </div>

      <CandidateInput
        v-model="source"
        :source="realSourceCandidates"
        :debounce-ms="150"
        label="记忆来源"
        placeholder="选择或输入来源名称"
        :flex="1.4"
        @choose="onEnter"
        @keydown.enter.prevent="onEnter"
      />

      <div class="field topk-field">
        <label for="episode-query-topk">返回条数</label>
        <input
          id="episode-query-topk"
          v-model.number="topk"
          class="input tabular"
          type="number"
          min="1"
          max="100"
          @keyup.enter="onEnter"
        />
      </div>
    </div>

    <div class="toolbar secondary-row">
      <CandidateInput
        v-model="person"
        :source="personCandidates"
        :debounce-ms="180"
        label="参与人物"
        placeholder="人物名称或 ID"
        :flex="1.4"
        @choose="onEnter"
        @keydown.enter.prevent="onEnter"
      />

      <div class="field" style="flex: 1 1 130px">
        <label for="episode-time-from">起始时间</label>
        <input
          id="episode-time-from"
          v-model="timeFrom"
          class="input tabular"
          placeholder="如 2026-05-01"
          @keyup.enter="onEnter"
        />
      </div>

      <div class="field" style="flex: 1 1 130px">
        <label for="episode-time-to">结束时间</label>
        <input
          id="episode-time-to"
          v-model="timeTo"
          class="input tabular"
          placeholder="如 2026-05-31"
          @keyup.enter="onEnter"
        />
      </div>

      <div class="query-btns">
        <button
          class="btn primary"
          type="button"
          :disabled="loading"
          @click="store.loadList()"
        >
          {{ loading ? "检索中…" : "查询情景" }}
        </button>
        <button
          class="btn"
          type="button"
          :disabled="loading"
          @click="store.resetFilters()"
        >
          重置条件
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.topk-field {
  max-width: 104px;
}

@media (max-width: 640px) {
  .topk-field {
    max-width: none;
  }
}

.secondary-row {
  margin-top: 10px;
}

.query-btns {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
</style>
