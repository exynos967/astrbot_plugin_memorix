<script setup lang="ts">
// People 组合面：左列查询 + 画像，右列人工覆盖。
// 从 legacy view-people（index.html 行 2281-2314）迁移为子组件组合。
// "候选列表"已移除——下拉候选与右侧列表重复，直接在输入框下拉选人触发查询。
import PersonQueryPanel from "@/components/people/PersonQueryPanel.vue";
import PersonProfilePanel from "@/components/people/PersonProfilePanel.vue";
import PersonOverridePanel from "@/components/people/PersonOverridePanel.vue";
import { usePeopleStore } from "@/stores/people";

const store = usePeopleStore();
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
