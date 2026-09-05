<script setup lang="ts">
// People 组合面：左列查询 + 画像，右列人工覆盖。
// 从 legacy view-people（index.html 行 2281-2314）迁移为子组件组合。
// "候选列表"已移除——下拉候选与右侧列表重复，直接在输入框下拉选人触发查询。
import PersonQueryPanel from "@/components/people/PersonQueryPanel.vue";
import PersonProfilePanel from "@/components/people/PersonProfilePanel.vue";
import PersonOverridePanel from "@/components/people/PersonOverridePanel.vue";
import PersonMemoryPanel from "@/components/people/PersonMemoryPanel.vue";
import { usePeopleStore } from "@/stores/people";

const store = usePeopleStore();
</script>

<template>
  <section class="view-people">
    <!-- 人物画像：查询 toolbar + 结果显示合并为一个 band -->
    <div class="band" style="margin-top: 0">
      <div class="panel-title"><h2>人物画像</h2></div>
      <PersonQueryPanel />
      <div class="profile-result">
        <PersonProfilePanel :profile="store.profile" />
      </div>
    </div>

    <PersonOverridePanel />
    <PersonMemoryPanel />
  </section>
</template>

<style scoped>
.view-people {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* 画像结果与上方 toolbar 间距 */
.profile-result {
  margin-top: 14px;
}
</style>
