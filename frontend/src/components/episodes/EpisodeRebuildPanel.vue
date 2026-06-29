<script setup lang="ts">
// Episode 重建面板：rebuild-source 输入（候选菜单 graph.nodeLabels）+ 重建按钮 + 重建结果文本。
// 从 legacy view-episodes 重建区（index.html 行 2213-2224, 4332-4363）迁移。
// 重建结果显示走 rebuildResultText；model 用 storeToRefs 解出的 ref。
import { ref } from "vue";
import { storeToRefs } from "pinia";
import { useEpisodeStore } from "@/stores/episode";
import { useGraphStore } from "@/stores/graph";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import { rebuildResultText } from "@/utils/episodeText";
import type { CandidateItem } from "@/stores/candidate";

const store = useEpisodeStore();
const graph = useGraphStore();
// storeToRefs 取出 ref，供候选菜单 model 绑定（store 代理会自动解包）
const { rebuildSource } = storeToRefs(store);

// rebuild-source 输入框 template ref，供候选菜单 attach
const sourceInput = ref<HTMLInputElement | null>(null);

/** 来源候选源：按 keyword 过滤 graph.nodeLabels。 */
function sourceCandidates(keyword: string): CandidateItem[] {
  const kw = keyword.trim().toLowerCase();
  return graph.nodeLabels
    .filter((label) => !kw || label.toLowerCase().includes(kw))
    .map((value) => ({ value, kind: "来源" }));
}

const sourceMenu = useCandidateMenu({
  inputRef: sourceInput,
  model: rebuildSource,
  source: sourceCandidates,
  debounceMs: 0,
});
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title"><h2>重建 Episode</h2></div>
    <div class="field">
      <label>来源</label>
      <input
        ref="sourceInput"
        v-model="store.rebuildSource"
        class="input"
        placeholder="source"
        @focus="sourceMenu.open"
        @input="sourceMenu.onInput"
      />
    </div>
    <div class="toolbar" style="margin-top: 10px">
      <button class="btn primary" :disabled="store.rebuilding" @click="store.rebuild()">
        重建 Episode
      </button>
    </div>
    <div v-if="store.rebuildResult" class="rebuild-result">
      <span class="section-label">重建结果</span>
      <p>{{ rebuildResultText(store.rebuildResult) }}</p>
    </div>
  </div>
</template>

<style scoped>
.rebuild-result {
  margin-top: 10px;
  padding: 8px 10px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  font-size: 13px;
}

.rebuild-result p {
  margin: 4px 0 0;
  color: var(--text);
}
</style>
