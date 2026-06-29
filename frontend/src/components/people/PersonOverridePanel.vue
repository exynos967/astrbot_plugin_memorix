<script setup lang="ts">
// 人工覆盖表单：Person ID（候选菜单）+ Override Text textarea + 保存/清除按钮。
// 从 legacy view-people 人工覆盖（index.html 行 2299-2307, 4485-4500）迁移。
// 直接读写 usePeopleStore.overrideId / overrideText；保存/清除走 store action。
import { ref } from "vue";
import { storeToRefs } from "pinia";
import { usePeopleStore } from "@/stores/people";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import { personCandidateValues } from "@/utils/personText";
import type { CandidateItem } from "@/stores/candidate";

const store = usePeopleStore();
// storeToRefs 取出 ref，供候选菜单 model 绑定
const { overrideId } = storeToRefs(store);

// Person ID 输入框 template ref，供候选菜单 attach
const idRef = ref<HTMLInputElement | null>(null);

// 候选来源：与查询面板一致，按关键词拉 registry 摊平为字符串集合
async function source(kw: string): Promise<CandidateItem[]> {
  const items = await store.loadCandidates(kw);
  return items.flatMap(personCandidateValues).map((value) => ({ value }));
}

const cm = useCandidateMenu({
  inputRef: idRef,
  model: overrideId,
  source,
  debounceMs: 180,
});
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title"><h2>人工覆盖</h2></div>
    <div class="field">
      <label>Person ID</label>
      <input
        ref="idRef"
        v-model="store.overrideId"
        class="input"
        placeholder="person_id"
        @focus="cm.open()"
        @input="cm.onInput()"
      />
    </div>
    <div class="field" style="margin-top: 10px">
      <label>Override Text</label>
      <textarea v-model="store.overrideText" class="textarea" rows="4"></textarea>
    </div>
    <div class="toolbar" style="margin-top: 10px">
      <button class="btn primary" :disabled="store.busy" @click="store.saveOverride()">保存覆盖</button>
      <button class="btn danger" :disabled="store.busy" @click="store.clearOverride()">清除覆盖</button>
    </div>
  </div>
</template>
