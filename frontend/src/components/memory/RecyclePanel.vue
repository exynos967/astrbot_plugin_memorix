<script setup lang="ts">
// 回收站面板：扁平 1px 细线行列表展示「关系 / 实体 / 段落」三种类型的已删除项并支持单条恢复。
import type { RecycleItem } from "@/services/memoryApi";
import {
  recycleDeletedAt,
  recycleDetail,
  recycleTitle,
  recycleType,
  recycleTypeLabel,
} from "@/utils/memoryText";

defineProps<{
  items: RecycleItem[];
  busy: boolean;
  loading?: boolean;
}>();

const emit = defineEmits<{
  restore: [hash: string, type: string];
  refresh: [];
}>();

function onRestore(item: RecycleItem): void {
  const hash = item.hash || "";
  if (hash) emit("restore", hash, recycleType(item));
}

function typeTone(type: "relation" | "entity" | "paragraph"): string {
  if (type === "paragraph") return "ok";
  if (type === "relation") return "";
  return "warn";
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div class="recycle-head">
        <h2>记忆回收站</h2>
        <span class="section-label tabular">共 {{ items.length }} 条可恢复项（段落 / 关系 / 实体）</span>
      </div>
      <button class="btn sm" type="button" :disabled="loading || busy" @click="emit('refresh')">
        {{ loading ? "刷新中…" : "刷新回收站" }}
      </button>
    </div>

    <div v-if="loading && !items.length" class="empty">正在读取回收站记录…</div>
    <div v-else-if="!items.length" class="empty">当前作用域回收站为空</div>
    <div v-else class="rows recycle-list">
      <article v-for="(item, idx) in items" :key="(item.hash || '') + idx" class="recycle-row">
        <div class="recycle-main">
          <h3 class="recycle-title">{{ recycleTitle(item) }}</h3>
          <p v-if="recycleDetail(item)" class="recycle-detail">{{ recycleDetail(item) }}</p>
          <div class="tags tabular">
            <span class="tag" :class="typeTone(recycleType(item))">
              {{ recycleTypeLabel(recycleType(item)) }}
            </span>
            <span v-if="item.source" class="tag">来源：{{ item.source }}</span>
            <span class="tag">删除于 {{ recycleDeletedAt(item) }}</span>
            <span v-if="item.hash" class="tag mono">哈希 {{ String(item.hash).slice(0, 12) }}</span>
          </div>
        </div>
        <button class="btn sm" type="button" :disabled="busy" @click="onRestore(item)">
          恢复记忆
        </button>
      </article>
    </div>
  </div>
</template>

<style scoped>
.recycle-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.recycle-list {
  border-top: 1px solid var(--hairline);
}

.recycle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  flex-wrap: wrap;
}

.recycle-main {
  flex: 1 1 260px;
  min-width: 0;
}

.recycle-title {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.recycle-detail {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--muted);
  overflow-wrap: anywhere;
}
</style>
