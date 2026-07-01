<script setup lang="ts">
// Episode 查询 toolbar：query 关键词 + source 输入（候选菜单 graph.nodeLabels）+ topk + 查询按钮。
// 从 legacy view-episodes toolbar（index.html 行 2203-2212）迁移。
// source 候选为同步 graph.nodeLabels，debounceMs 0；model 用 storeToRefs 解出的 ref。
import { ref } from "vue";
import { storeToRefs } from "pinia";
import { useEpisodeStore } from "@/stores/episode";
import { useGraphStore } from "@/stores/graph";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import type { CandidateItem } from "@/stores/candidate";

const store = useEpisodeStore();
const graph = useGraphStore();
// 取 ref 本身：useCandidateMenu 需写回 model.value；store 代理会自动解包，故用 storeToRefs
const { source } = storeToRefs(store);

// source 输入框 template ref，供候选菜单 attach
const sourceInput = ref<HTMLInputElement | null>(null);

/** 来源候选源：按 keyword 过滤 graph.nodeLabels，keyword 为空返回全量。 */
function sourceCandidates(keyword: string): CandidateItem[] {
  const kw = keyword.trim().toLowerCase();
  return graph.nodeLabels
    .filter((label) => !kw || label.toLowerCase().includes(kw))
    .slice(0, 20)
    .map((value) => ({ value, kind: "来源" }));
}

const sourceMenu = useCandidateMenu({
  inputRef: sourceInput,
  model: source,
  source: sourceCandidates,
  debounceMs: 0,
});
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title"><h2>情景记忆</h2></div>
    <div class="toolbar">
      <div class="field" style="flex: 2">
        <label>关键词</label>
        <input v-model="store.query" class="input" placeholder="episode 关键词" />
      </div>
      <div class="field">
        <label>来源</label>
        <input
          ref="sourceInput"
          v-model="store.source"
          class="input"
          placeholder="source"
          @focus="sourceMenu.open"
          @input="sourceMenu.onInput"
        />
      </div>
      <div class="field">
        <label>Top K</label>
        <input v-model.number="store.topk" class="input" type="number" min="1" max="50" />
      </div>
      <button class="btn primary" :disabled="store.loading" @click="store.loadList()">查询</button>
    </div>
  </div>
</template>
