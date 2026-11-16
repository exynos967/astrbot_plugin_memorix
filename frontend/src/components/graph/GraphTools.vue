<script setup lang="ts">
// Graph 视图第二行操作栏：CRUD 弹窗入口、布局控制（暂停模拟/重置高亮/恢复布局/性能模式）、缩放以及聚焦来源退出胶囊。
import { computed, inject } from "vue";
import { storeToRefs } from "pinia";
import { useGraphStore } from "@/stores/graph";
import { GRAPH_VIS_KEY, type VisController } from "@/composables/useVisNetwork";
import { scopeLabel } from "@/utils/graphText";

defineProps<{
  addNodeOpen: boolean;
  addEdgeOpen: boolean;
  dictOpen: boolean;
  sourceBrowserOpen: boolean;
}>();

const emit = defineEmits<{
  (e: "update:addNodeOpen", v: boolean): void;
  (e: "update:addEdgeOpen", v: boolean): void;
  (e: "update:dictOpen", v: boolean): void;
  (e: "update:sourceBrowserOpen", v: boolean): void;
}>();

const store = useGraphStore();
const vis = inject<VisController | null>(GRAPH_VIS_KEY, null);

const {
  sourceFocus,
  currentScope,
  resolvedScope,
  scopeOptions,
  simulationRunning,
  lowPerf,
  zoom,
  highlightedNode,
  layoutSnapshot,
} = storeToRefs(store);

const hasSnapshot = computed(() => Object.keys(layoutSnapshot.value || {}).length > 0);

const scopeChipText = computed(() => {
  if (currentScope.value) {
    return `作用域：${scopeLabel(currentScope.value, scopeOptions.value)}`;
  }
  return resolvedScope.value
    ? `作用域：${scopeLabel(resolvedScope.value, scopeOptions.value)}（自动）`
    : "作用域：自动";
});

function toggleSim(): void {
  vis?.toggleSimulation();
}

function togglePerf(): void {
  vis?.applyLowPerf();
}

function onFit(): void {
  vis?.fitGraphView(true);
  store.userZoomed = false;
}

function onResetHighlight(): void {
  vis?.resetHighlight();
}

function onRestoreLayout(): void {
  vis?.restoreLayout();
}

function clearSourceFocus(): void {
  store.sourceFocus = "";
  void store.loadGraph();
}
</script>

<template>
  <div class="graph-tools">
    <button class="btn" type="button" @click="emit('update:addNodeOpen', true)">新增节点</button>
    <button class="btn" type="button" @click="emit('update:addEdgeOpen', true)">新增关系</button>
    <button class="btn" type="button" @click="emit('update:dictOpen', true)">内容字典</button>
    <button class="btn" type="button" @click="emit('update:sourceBrowserOpen', true)">
      来源批次
    </button>
    <button
      class="btn"
      type="button"
      :class="{ 'btn-toggled': !simulationRunning }"
      :aria-pressed="!simulationRunning"
      @click="toggleSim"
    >
      {{ simulationRunning ? "暂停模拟" : "继续布局" }}
    </button>
    <button
      class="btn"
      type="button"
      :disabled="!highlightedNode"
      title="清除邻域高亮并恢复常规配色"
      @click="onResetHighlight"
    >
      重置高亮
    </button>
    <button
      class="btn"
      type="button"
      :disabled="!hasSnapshot"
      title="回滚节点坐标至初始稳定布局"
      @click="onRestoreLayout"
    >
      恢复布局
    </button>
    <button
      class="btn"
      type="button"
      :class="{ 'btn-toggled': lowPerf }"
      :aria-pressed="lowPerf"
      @click="togglePerf"
    >
      性能模式
    </button>

    <span class="graph-zoom-inline" role="group" aria-label="图谱缩放">
      <button
        class="btn icon"
        type="button"
        title="缩小图谱"
        aria-label="缩小图谱"
        @click="vis?.adjustZoom(-0.15)"
      >
        -
      </button>
      <button
        class="btn icon"
        type="button"
        title="放大图谱"
        aria-label="放大图谱"
        @click="vis?.adjustZoom(0.15)"
      >
        +
      </button>
      <button
        class="btn icon"
        type="button"
        title="适配画布视野"
        aria-label="适配画布视野"
        @click="onFit"
      >
        ◰
      </button>
      <span class="zoom-value">{{ Math.round(zoom * 100) }}%</span>
    </span>

    <span class="tag" :class="{ ok: !!currentScope }">{{ scopeChipText }}</span>

    <span v-if="sourceFocus" class="source-focus-pill">
      <span class="focus-text">聚焦来源：{{ sourceFocus }}</span>
      <button
        type="button"
        class="focus-exit-btn"
        title="退出聚焦来源并恢复完整图谱"
        @click="clearSourceFocus"
      >
        × 退出聚焦
      </button>
    </span>
  </div>
</template>

<style scoped>
.btn-toggled {
  color: var(--accent-ink);
  background: var(--accent-soft);
  border-color: var(--hairline-strong);
}

.source-focus-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
  padding: 3px 6px 3px 12px;
  border-radius: 999px;
  border: 1px solid var(--hairline-strong);
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 12px;
  font-weight: 600;
}

.focus-text {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.focus-exit-btn {
  border: 1px solid var(--hairline);
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
  padding: 2px 9px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.focus-exit-btn:hover {
  border-color: var(--hairline-strong);
  background: var(--surface);
}
</style>
