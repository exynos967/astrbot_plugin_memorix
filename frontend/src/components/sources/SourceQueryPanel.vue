<script setup lang="ts">
// 来源查询 toolbar：节点 / 边 source / 边 target 三输入 + 载入来源按钮。
// 从 legacy view-sources toolbar（index.html 行 2316-2324, 4502-4517）迁移。
// 三个输入各接一个 useCandidateMenu 实例，候选源统一为 graph.nodeLabels（P8 loadGraph 后填充）。
// 输入值直写 store.nodeId/edgeFrom/edgeTo；按钮触发 store.load()。
import { ref } from "vue";
import { storeToRefs } from "pinia";
import { useSourcesStore } from "@/stores/sources";
import { useGraphStore } from "@/stores/graph";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import type { CandidateItem } from "@/stores/candidate";

const store = useSourcesStore();
const graph = useGraphStore();
// 取 ref 本身：useCandidateMenu 需写回 model.value；store 代理会自动解包，故用 storeToRefs
const { nodeId, edgeFrom, edgeTo } = storeToRefs(store);

// 三个输入的 template ref
const nodeInput = ref<HTMLInputElement | null>(null);
const edgeFromInput = ref<HTMLInputElement | null>(null);
const edgeToInput = ref<HTMLInputElement | null>(null);

/** graph-node 候选源：按 keyword 过滤 nodeLabels，keyword 为空返回全量。 */
function nodeCandidates(keyword: string): CandidateItem[] {
  const kw = keyword.trim().toLowerCase();
  return graph.nodeLabels
    .filter((label) => !kw || label.toLowerCase().includes(kw))
    .map((value) => ({ value, kind: "节点" }));
}

// 三个输入各持独立候选会话；debounceMs 0（同步来源，立即刷新）
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
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="toolbar">
      <div class="field">
        <label>节点</label>
        <input
          ref="nodeInput"
          v-model="nodeId"
          class="input"
          placeholder="entity"
          @focus="nodeMenu.open"
          @input="nodeMenu.onInput"
        />
      </div>
      <div class="field">
        <label>边 source</label>
        <input
          ref="edgeFromInput"
          v-model="edgeFrom"
          class="input"
          placeholder="from"
          @focus="edgeFromMenu.open"
          @input="edgeFromMenu.onInput"
        />
      </div>
      <div class="field">
        <label>边 target</label>
        <input
          ref="edgeToInput"
          v-model="edgeTo"
          class="input"
          placeholder="to"
          @focus="edgeToMenu.open"
          @input="edgeToMenu.onInput"
        />
      </div>
      <button class="btn primary" :disabled="store.loading" @click="store.load()">载入来源</button>
    </div>
  </div>
</template>
