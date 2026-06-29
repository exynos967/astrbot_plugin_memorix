<script setup lang="ts">
// People 组合面：左列查询 toolbar + 画像结果，右列人工覆盖 + 候选人物列表。
// 从 legacy view-people（index.html 行 2281-2314）迁移为子组件组合。
// onPick 回填 keyword + overrideId 并触发 query（对齐 legacy pickPerson）；
// onMounted 预热候选 registry。
import { onMounted } from "vue";
import PersonQueryPanel from "@/components/people/PersonQueryPanel.vue";
import PersonProfilePanel from "@/components/people/PersonProfilePanel.vue";
import PersonOverridePanel from "@/components/people/PersonOverridePanel.vue";
import PersonListPanel from "@/components/people/PersonListPanel.vue";
import { usePeopleStore } from "@/stores/people";

const store = usePeopleStore();

function onPick(payload: { personId: string; keyword: string }): void {
  // 回填 keyword + overrideId，触发画像查询（对齐 legacy pickPerson）
  store.keyword = payload.keyword || payload.personId;
  store.overrideId = payload.personId;
  void store.query();
}

onMounted(() => {
  void store.loadCandidates();
});
</script>

<template>
  <section class="view-people">
    <div class="grid-2">
      <div class="people-col">
        <PersonQueryPanel />
        <PersonProfilePanel :profile="store.profile" />
      </div>
      <div class="people-col">
        <PersonOverridePanel />
        <PersonListPanel :items="store.candidates" @pick="onPick" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.view-people {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.people-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>
