<script setup lang="ts">
// 画像查询 toolbar：关键词输入（下拉候选）+ TopK + 查询按钮。
// 从 legacy view-people toolbar（index.html 行 2284-2292）迁移。
// 下拉候选选中即触发查询，"候选列表"按钮已移除（与下拉重复）。
import { ref } from "vue";
import { storeToRefs } from "pinia";
import { usePeopleStore } from "@/stores/people";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import { personCandidateValues } from "@/utils/personText";
import type { CandidateItem } from "@/stores/candidate";

const store = usePeopleStore();
const { keyword } = storeToRefs(store);

const kwRef = ref<HTMLInputElement | null>(null);

async function source(kw: string): Promise<CandidateItem[]> {
  const items = await store.suggestPersons(kw);
  return items.flatMap(personCandidateValues).map((value) => ({ value }));
}

const cm = useCandidateMenu({
  inputRef: kwRef,
  model: keyword,
  source,
  debounceMs: 180,
  /** 下拉选中某人 → 回填 keyword + 立即查询。 */
  onChoose: (item) => {
    store.keyword = item.value;
    void store.query();
  },
});
</script>

<template>
  <div class="toolbar people-query-toolbar">
    <div class="field" style="flex: 2">
      <label>人物关键词</label>
      <input
        ref="kwRef"
        v-model="store.keyword"
        class="input"
        placeholder="姓名、别名或 person_id"
        @focus="cm.open()"
        @input="cm.onInput()"
      />
    </div>
    <div class="field">
      <label>Top K</label>
      <input v-model.number="store.topk" class="input" type="number" min="1" max="50" />
    </div>
    <button class="btn primary" :disabled="store.querying" @click="store.query()">查询画像</button>
  </div>
</template>
