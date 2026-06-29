<script setup lang="ts">
// 画像查询 toolbar：关键词输入（候选菜单 + 180ms 防抖）+ TopK + 查询/候选列表按钮。
// 从 legacy view-people toolbar（index.html 行 2284-2292）迁移。
// 直接读写 usePeopleStore；候选来源经 personCandidateValues 摊平为字符串列表。
import { ref } from "vue";
import { storeToRefs } from "pinia";
import { usePeopleStore } from "@/stores/people";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import { personCandidateValues } from "@/utils/personText";
import type { CandidateItem } from "@/stores/candidate";

const store = usePeopleStore();
// storeToRefs 取出 ref，供候选菜单 model 绑定（store 直接取值为解包后的 string）
const { keyword } = storeToRefs(store);

// 关键词输入框 template ref，供候选菜单 attach
const kwRef = ref<HTMLInputElement | null>(null);

// 候选来源：按关键词拉 registry，摊平为候选字符串集合
async function source(kw: string): Promise<CandidateItem[]> {
  const items = await store.loadCandidates(kw);
  return items.flatMap(personCandidateValues).map((value) => ({ value }));
}

const cm = useCandidateMenu({
  inputRef: kwRef,
  model: keyword,
  source,
  debounceMs: 180,
});
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title"><h2>人物画像</h2></div>
    <div class="toolbar">
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
      <button class="btn" :disabled="store.loadingCandidates" @click="store.refreshCandidates()">候选列表</button>
    </div>
  </div>
</template>
