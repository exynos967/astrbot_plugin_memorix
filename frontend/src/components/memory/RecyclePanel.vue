<script setup lang="ts">
// 回收站面板：列表 + 恢复按钮。
// 从 legacy renderRecycleResults（index.html 行 4640-4664）迁移。
// 恢复上抛 restore(hash, type)，数据从 props.items 读。
import type { RecycleItem } from "@/services/memoryApi";
import { recycleDeletedAt, recycleDetail, recycleTitle, recycleType } from "@/utils/memoryText";

defineProps<{
  items: RecycleItem[];
  busy: boolean;
}>();

const emit = defineEmits<{ restore: [hash: string, type: string] }>();

function onRestore(item: RecycleItem): void {
  const hash = item.hash || "";
  if (hash) emit("restore", hash, recycleType(item));
}

function typeLabel(type: string): string {
  return type === "relation" ? "关系" : "实体";
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>回收站</h2>
      <div class="toolbar">
        <span class="section-label">{{ items.length }} 条</span>
      </div>
    </div>
    <div class="result-list">
      <div v-if="!items.length" class="empty">回收站为空</div>
      <div v-for="(item, idx) in items" :key="(item.hash || '') + idx" class="result">
        <div class="memory-recycle-row">
          <div>
            <div class="result-head"><h3>{{ recycleTitle(item) }}</h3></div>
            <p v-if="recycleDetail(item)">{{ recycleDetail(item) }}</p>
            <div class="tags">
              <span class="tag">{{ typeLabel(recycleType(item)) }}</span>
              <span class="tag">删除于 {{ recycleDeletedAt(item) }}</span>
              <span v-if="item.hash" class="tag mono">{{ String(item.hash).slice(0, 12) }}</span>
            </div>
          </div>
          <button class="btn" :disabled="busy" @click="onRestore(item)">恢复</button>
        </div>
      </div>
    </div>
  </div>
</template>
