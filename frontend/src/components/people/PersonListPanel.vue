<script setup lang="ts">
// 候选人物列表：每项 display_name 作标题、aliases(最多6) 作 tag、"选择"按钮上抛 pick。
// 从 legacy listPeople 渲染（index.html 行 4471-4476）迁移。
// 纯展示（props down），选择事件上抛 { personId, keyword } 由 PeopleView 联动查询。
import type { PersonRegistryItem } from "@/services/personApi";
import { personAliases, personDisplayName } from "@/utils/personText";

defineProps<{ items: PersonRegistryItem[] }>();

const emit = defineEmits<{
  pick: [payload: { personId: string; keyword: string }];
}>();

function onPick(item: PersonRegistryItem): void {
  emit("pick", { personId: item.person_id, keyword: personDisplayName(item) || item.person_id });
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>候选人物</h2>
      <div class="toolbar">
        <span class="section-label">{{ items.length }} 条</span>
      </div>
    </div>
    <div class="result-list">
      <div v-if="!items.length" class="empty">暂无候选</div>
      <div v-for="item in items" :key="item.person_id" class="result">
        <div>
          <div class="result-head">
            <h3>{{ personDisplayName(item) }}</h3>
          </div>
          <div v-if="personAliases(item).length" class="tags">
            <span v-for="alias in personAliases(item).slice(0, 6)" :key="alias" class="tag">{{ alias }}</span>
          </div>
        </div>
        <button class="btn" @click="onPick(item)">选择</button>
      </div>
    </div>
  </div>
</template>
