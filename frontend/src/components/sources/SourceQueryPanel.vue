<script setup lang="ts">
// 来源筛选栏：支持按实体节点或成对关系边（起点 + 终点）检索段落，支持回车提交与一键重置筛选。
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import { useSourcesStore } from "@/stores/sources";
import { useGraphStore } from "@/stores/graph";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import type { CandidateItem } from "@/stores/candidate";

const store = useSourcesStore();
const graph = useGraphStore();
const { nodeId, edgeFrom, edgeTo, loading } = storeToRefs(store);

const nodeInput = ref<HTMLInputElement | null>(null);
const edgeFromInput = ref<HTMLInputElement | null>(null);
const edgeToInput = ref<HTMLInputElement | null>(null);

const hasFilter = computed(() =>
  Boolean(nodeId.value.trim() || edgeFrom.value.trim() || edgeTo.value.trim()),
);

function nodeCandidates(keyword: string): CandidateItem[] {
  const kw = keyword.trim().toLowerCase();
  return graph.nodeLabels
    .filter((label) => !kw || label.toLowerCase().includes(kw))
    .slice(0, 24)
    .map((value) => ({ value, kind: "实体" }));
}

const nodeMenu = useCandidateMenu({
  inputRef: nodeInput,
  model: nodeId,
  source: nodeCandidates,
  debounceMs: 0,
});
const edgeFromMenu = useCandidateMenu({
  inputRef: edgeFromInput,
  model: edgeFrom,
  source: nodeCandidates,
  debounceMs: 0,
});
const edgeToMenu = useCandidateMenu({
  inputRef: edgeToInput,
  model: edgeTo,
  source: nodeCandidates,
  debounceMs: 0,
});

function onEnter(): void {
  if (!loading.value) void store.load();
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>来源与证据段落筛选</h2>
      <span class="section-label">留空显示来源批次汇总；按关系边筛选需同时填写起点与终点</span>
    </div>

    <div class="toolbar">
      <div class="field">
        <label for="source-filter-node">实体节点</label>
        <input
          id="source-filter-node"
          ref="nodeInput"
          v-model="nodeId"
          class="input"
          placeholder="可选：实体名称"
          @focus="nodeMenu.open"
          @input="nodeMenu.onInput"
          @keyup.enter="onEnter"
        />
      </div>

      <div class="field">
        <label for="source-filter-edge-from">关系边起点</label>
        <input
          id="source-filter-edge-from"
          ref="edgeFromInput"
          v-model="edgeFrom"
          class="input"
          placeholder="成对填写：主体起点"
          @focus="edgeFromMenu.open"
          @input="edgeFromMenu.onInput"
          @keyup.enter="onEnter"
        />
      </div>

      <div class="field">
        <label for="source-filter-edge-to">关系边终点</label>
        <input
          id="source-filter-edge-to"
          ref="edgeToInput"
          v-model="edgeTo"
          class="input"
          placeholder="成对填写：客体终点"
          @focus="edgeToMenu.open"
          @input="edgeToMenu.onInput"
          @keyup.enter="onEnter"
        />
      </div>

      <button class="btn primary" type="button" :disabled="loading" @click="store.load()">
        {{ loading ? "查询中…" : "查询来源" }}
      </button>

      <button
        class="btn"
        type="button"
        :disabled="loading || !hasFilter"
        @click="store.resetFilters()"
      >
        重置条件
      </button>
    </div>
  </div>
</template>
