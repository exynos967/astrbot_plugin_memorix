<script setup lang="ts">
// 来源批次浏览弹窗：列出所有来源（summary 模式），
// 支持聚焦某来源子图（设 store.sourceFocus + loadGraph）与批量删除来源。
// 从 legacy showGraphSourceBrowser / focusGraphSource（index.html 行 4186-4226）迁移。
import { ref, watch } from "vue";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import { fetchSourceList, batchDeleteSource, type SourceSummaryItem } from "@/services/sourceApi";
import { formatTs } from "@/utils/time";

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const graphStore = useGraphStore();
const app = useAppStore();
const logs = useLogsStore();

// summary 模式来源列表
const sources = ref<SourceSummaryItem[]>([]);
const loading = ref(false);

/** 关闭弹窗。 */
function close(): void {
  emit("update:modelValue", false);
}

/** 拉取来源批次列表（summary 模式）。 */
async function loadSources(): Promise<void> {
  loading.value = true;
  try {
    const data = await fetchSourceList({});
    sources.value = (data.sources || []) as SourceSummaryItem[];
  } catch (err) {
    app.pushError(err instanceof Error ? err.message : String(err), "GraphSourceBrowserDialog.loadSources");
  } finally {
    loading.value = false;
  }
}

// 打开时拉取列表
watch(
  () => props.modelValue,
  (v) => {
    if (v) void loadSources();
  },
);

/** 聚焦该来源子图：设 sourceFocus 后重载图谱并关闭弹窗。 */
function focusSource(source: string): void {
  graphStore.sourceFocus = source;
  void graphStore.loadGraph();
  close();
}

/** 删除来源：确认后调 batchDeleteSource，成功重新拉取列表。 */
async function deleteSource(source: string): Promise<void> {
  if (!source) return;
  const confirmed = await app.requestConfirmation({
    title: "删除来源",
    message: `确定删除 source ${source} 及其关联数据？`,
    confirmText: "删除",
    danger: true,
  });
  if (!confirmed) return;
  try {
    const res = await batchDeleteSource(source);
    logs.log(`source 已删除: ${source}（count=${res.count ?? 0}）`);
    // 若当前聚焦即被删来源，清空聚焦
    if (graphStore.sourceFocus === source) {
      graphStore.sourceFocus = "";
      void graphStore.loadGraph();
    }
    await loadSources();
  } catch (err) {
    app.pushError(err instanceof Error ? err.message : String(err), "GraphSourceBrowserDialog.deleteSource");
  }
}
</script>

<template>
  <div v-if="modelValue" class="dialog-overlay" @click.self="close">
    <div class="dialog">
      <div class="panel-title">
        <h2>来源批次</h2>
      </div>

      <div v-if="loading" class="empty">加载中</div>
      <div v-else-if="!sources.length" class="empty">暂无来源批次</div>
      <div v-else class="result-list">
        <div v-for="item in sources" :key="item.source || ''" class="result">
          <div class="result-head">
            <h3>{{ item.source || "source" }}</h3>
          </div>
          <div class="tags">
            <span v-if="item.count != null" class="tag">{{ Number(item.count || 0) }} 段</span>
            <span v-if="item.last_updated" class="tag">{{ formatTs(item.last_updated) }}</span>
          </div>
          <div class="result-actions">
            <button class="btn primary" @click="focusSource(item.source || '')">查看</button>
            <button class="btn danger" @click="deleteSource(item.source || '')">删除</button>
          </div>
        </div>
      </div>

      <div class="toolbar" style="justify-content: flex-end">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.dialog {
  min-width: 420px;
  max-width: 90vw;
  max-height: 80vh;
  overflow: auto;
  background: var(--bg-panel, #fff);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
