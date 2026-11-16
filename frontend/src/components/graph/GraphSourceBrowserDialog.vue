<script setup lang="ts">
// 来源批次浏览弹窗：全量接入 effectiveScope 隔离，扁平细线行列表展示，支持聚焦来源子图、退出聚焦与 Esc 安全关闭。
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import {
  batchDeleteSource,
  fetchSourceList,
  type SourceSummaryItem,
} from "@/services/sourceApi";
import { formatTs } from "@/utils/time";
import { errText } from "@/utils/error";

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const graphStore = useGraphStore();
const app = useAppStore();
const logs = useLogsStore();

const sources = ref<SourceSummaryItem[]>([]);
const filter = ref("");
const loading = ref(false);
const deletingKey = ref("");

const filteredSources = computed(() => {
  const kw = filter.value.trim().toLowerCase();
  const valid = sources.value.filter((item) => item.source);
  if (!kw) return valid;
  return valid.filter((item) => String(item.source || "").toLowerCase().includes(kw));
});

function close(): void {
  if (deletingKey.value) return;
  emit("update:modelValue", false);
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape" && !app.confirmation) {
    e.preventDefault();
    close();
  }
}

let loadSeq = 0;
async function loadSources(): Promise<void> {
  const seq = ++loadSeq;
  loading.value = true;
  try {
    const data = await fetchSourceList({}, graphStore.effectiveScope());
    if (seq !== loadSeq) return;
    sources.value = (data.sources || []) as SourceSummaryItem[];
  } catch (err) {
    if (seq !== loadSeq) return;
    app.pushError(errText(err), "loadGraphSources");
    sources.value = [];
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      filter.value = "";
      window.addEventListener("keydown", onKeydown);
      void loadSources();
    } else {
      window.removeEventListener("keydown", onKeydown);
    }
  },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});

function focusSource(source: string): void {
  if (!source) return;
  graphStore.sourceFocus = source;
  void graphStore.loadGraph();
  close();
}

function exitFocus(): void {
  graphStore.sourceFocus = "";
  void graphStore.loadGraph();
  close();
}

async function deleteSource(source: string): Promise<void> {
  if (!source || deletingKey.value) return;
  const scope = graphStore.effectiveScope();
  const confirmed = await app.requestConfirmation({
    title: "删除来源批次",
    message: `确定删除当前作用域下的来源「${source}」及其关联段落与孤立图谱关系？`,
    confirmText: "确认删除",
    danger: true,
  });
  if (!confirmed) return;

  deletingKey.value = source;
  try {
    const res = await batchDeleteSource(source, scope);
    const pCount = res?.deleted?.paragraph ?? res?.count ?? 0;
    const rCount = res?.deleted?.relation ?? 0;
    logs.log(`来源批次已删除 [${source}]：清理 ${pCount} 段，剪枝 ${rCount} 关系`, "info");
    app.pushSuccess?.(`已删除来源「${source}」`, "deleteSource");
    if (graphStore.sourceFocus === source) {
      graphStore.sourceFocus = "";
    }
    await Promise.allSettled([loadSources(), graphStore.loadGraph()]);
  } catch (err) {
    app.pushError(errText(err), "deleteGraphSource");
  } finally {
    deletingKey.value = "";
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="dialog-overlay" @click.self="close">
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="src-browser-title">
        <div class="panel-title">
          <h2 id="src-browser-title">来源批次列表</h2>
          <button
            class="btn ghost sm"
            type="button"
            aria-label="关闭弹窗"
            :disabled="!!deletingKey"
            @click="close"
          >
            关闭
          </button>
        </div>

        <div class="field">
          <label for="graph-source-filter">按来源名称筛选</label>
          <input
            id="graph-source-filter"
            v-model="filter"
            class="input"
            placeholder="输入来源名称关键字…"
          />
        </div>

        <div class="source-list">
          <div v-if="loading" class="empty">正在读取当前作用域的来源批次…</div>
          <div v-else-if="!filteredSources.length" class="empty">
            {{ sources.length ? "没有匹配的来源批次" : "当前作用域暂无来源批次" }}
          </div>
          <div v-else class="rows">
            <div v-for="item in filteredSources" :key="item.source || ''" class="source-row">
              <div class="source-main">
                <div class="source-head">
                  <strong>{{ item.source }}</strong>
                  <span v-if="graphStore.sourceFocus === item.source" class="tag ok">
                    当前聚焦
                  </span>
                </div>
                <div class="tags">
                  <span v-if="item.count != null" class="tag tabular">{{ Number(item.count || 0) }} 条段落</span>
                  <span v-if="item.last_updated" class="tag tabular">
                    更新于 {{ formatTs(item.last_updated) }}
                  </span>
                </div>
              </div>
              <div class="source-actions">
                <button
                  v-if="graphStore.sourceFocus === item.source"
                  class="btn sm"
                  type="button"
                  :disabled="!!deletingKey"
                  @click="exitFocus"
                >
                  退出聚焦
                </button>
                <button
                  v-else
                  class="btn sm"
                  type="button"
                  :disabled="!!deletingKey"
                  @click="focusSource(item.source || '')"
                >
                  聚焦子图
                </button>
                <button
                  class="btn danger sm"
                  type="button"
                  :disabled="!!deletingKey"
                  @click="deleteSource(item.source || '')"
                >
                  {{ deletingKey === item.source ? "删除中…" : "删除批次" }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <div class="dialog-actions">
          <button
            v-if="graphStore.sourceFocus"
            class="btn"
            type="button"
            :disabled="!!deletingKey"
            @click="exitFocus"
          >
            退出当前来源聚焦
          </button>
          <button class="btn" type="button" :disabled="!!deletingKey" @click="close">关闭</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: var(--bg-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9000;
  padding: 16px;
}

.dialog {
  width: min(560px, 100%);
  max-height: 82vh;
  background: var(--bg-elevated);
  color: var(--text);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  padding: 20px 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
}

.source-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
  border-top: 1px solid var(--hairline);
}

.source-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 0;
  flex-wrap: wrap;
}

.source-main {
  flex: 1 1 220px;
  min-width: 0;
}

.source-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.source-head strong {
  font-size: 13.5px;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.source-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--hairline);
}
</style>
