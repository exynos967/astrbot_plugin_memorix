<script setup lang="ts">
// People 组合面：左栏可折叠「人物目录」+ 右栏「画像查询与证据链 + 人工画像覆盖 + 别名与事实账本」。
import { onMounted, ref } from "vue";
import PersonListPanel from "@/components/people/PersonListPanel.vue";
import PersonQueryPanel from "@/components/people/PersonQueryPanel.vue";
import PersonProfilePanel from "@/components/people/PersonProfilePanel.vue";
import PersonOverridePanel from "@/components/people/PersonOverridePanel.vue";
import PersonMemoryPanel from "@/components/people/PersonMemoryPanel.vue";
import { usePeopleStore } from "@/stores/people";

const store = usePeopleStore();
const showDirectory = ref(true);

onMounted(() => {
  void store.loadRegistry(1, store.registryKeyword);
});
</script>

<template>
  <section class="view-people" :class="{ 'with-sidebar': showDirectory }">
    <PersonListPanel v-if="showDirectory" />

    <div class="people-main-col">
      <div class="band">
        <div class="panel-title">
          <div>
            <h2>人物画像查询</h2>
            <span class="section-label">聚合长期记忆、知识关系与结构化事实生成人物画像</span>
          </div>
          <button
            class="btn sm"
            type="button"
            :aria-expanded="showDirectory"
            @click="showDirectory = !showDirectory"
          >
            {{ showDirectory ? "收起人物目录" : `展开人物目录（${store.registryTotal || store.candidates.length}）` }}
          </button>
        </div>
        <PersonQueryPanel />
        <div class="profile-result">
          <PersonProfilePanel :profile="store.profile" />
        </div>
      </div>

      <PersonOverridePanel />
      <PersonMemoryPanel />
    </div>
  </section>
</template>

<style scoped>
.view-people {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

@media (min-width: 1024px) {
  .view-people.with-sidebar {
    grid-template-columns: 310px minmax(0, 1fr);
  }
}

.people-main-col {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.view-people :deep(.band) {
  margin-top: 0;
}

.profile-result {
  margin-top: 14px;
}
</style>
