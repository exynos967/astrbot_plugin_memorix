<script setup lang="ts">
// Graph 视图主协调器：vis-network 画布 + 工具栏 + 详情面板 + 弹窗组。
// 修复：
// - 恢复 .graph-empty 内按钮的 pointer-events: auto，修复「重试 / 退出聚焦」按钮无法点击的 Bug。
// - 窄屏 (< 980px) 为画布保留最小高度并限制详情面板最大高度 42vh，杜绝画布被挤压为 0px。
// - 监听 effectiveScope 变化，无论是顶部全局栏还是图谱工具栏切换作用域均自动刷新图谱。
import { nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useGraphStore } from "@/stores/graph";
import { useVisNetwork, GRAPH_VIS_KEY } from "@/composables/useVisNetwork";
import GraphToolbar from "@/components/graph/GraphToolbar.vue";
import GraphTools from "@/components/graph/GraphTools.vue";
import GraphDetailPanel from "@/components/graph/GraphDetailPanel.vue";
import GraphAddNodeDialog from "@/components/graph/GraphAddNodeDialog.vue";
import GraphAddEdgeDialog from "@/components/graph/GraphAddEdgeDialog.vue";
import GraphDictionaryDialog from "@/components/graph/GraphDictionaryDialog.vue";
import GraphSourceBrowserDialog from "@/components/graph/GraphSourceBrowserDialog.vue";

const store = useGraphStore();
const { loading, initError } = storeToRefs(store);

const canvasRef = ref<HTMLDivElement | null>(null);
let resizeObserver: ResizeObserver | null = null;
let resizeRaf: number | null = null;
let initialLoaded = false;

const rendering = ref(false);

const vis = useVisNetwork({
  onNodeSelect: (id) => {
    store.selectedNode = id;
    store.selectedEdgeId = "";
  },
  onEdgeSelect: (id) => {
    store.selectedEdgeId = id;
    store.selectedNode = "";
  },
  onCanvasClear: () => {
    store.selectedNode = "";
    store.selectedEdgeId = "";
  },
});
provide(GRAPH_VIS_KEY, vis);

const addNodeOpen = ref(false);
const addEdgeOpen = ref(false);
const dictOpen = ref(false);
const sourceBrowserOpen = ref(false);

function renderWhenReady(): void {
  const el = canvasRef.value;
  if (!el) return;
  rendering.value = true;
  nextTick(() => {
    window.requestAnimationFrame(() => {
      if (canvasRef.value && canvasRef.value.clientWidth > 0) {
        try {
          vis.renderGraph(canvasRef.value);
          rendering.value = false;
        } catch (err) {
          rendering.value = false;
          store.initError = err instanceof Error ? err.message : String(err);
        }
      } else {
        rendering.value = false;
      }
    });
  });
}

function scheduleCanvasResize(): void {
  if (resizeRaf != null) return;
  resizeRaf = window.requestAnimationFrame(() => {
    resizeRaf = null;
    const el = canvasRef.value;
    if (!el || el.clientWidth <= 0 || el.clientHeight <= 0 || !store.rawNodes.length) return;
    if (!vis.ready.value) {
      renderWhenReady();
      return;
    }
    vis.resizeGraphView(false);
  });
}

async function loadAndRender(): Promise<void> {
  await store.loadGraph();
}

onMounted(async () => {
  const el = canvasRef.value;
  if (el && typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => {
      scheduleCanvasResize();
    });
    resizeObserver.observe(el);
  }
  await store.loadScopes();
  initialLoaded = true;
  await loadAndRender();
});

watch(
  () => store.rawNodes,
  () => {
    if (!initError.value) renderWhenReady();
  },
);

watch(
  () => store.effectiveScope(),
  (next, prev) => {
    if (!initialLoaded || next === prev) return;
    void loadAndRender();
  },
);

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (resizeRaf != null) {
    window.cancelAnimationFrame(resizeRaf);
    resizeRaf = null;
  }
});

function retryInit(): void {
  store.initError = "";
  void loadAndRender();
}

function exitSourceFocus(): void {
  store.sourceFocus = "";
  void loadAndRender();
}
</script>

<template>
  <section class="view-graph">
    <div class="band" style="margin-top: 0">
      <GraphToolbar />
      <GraphTools
        v-model:addNodeOpen="addNodeOpen"
        v-model:addEdgeOpen="addEdgeOpen"
        v-model:dictOpen="dictOpen"
        v-model:sourceBrowserOpen="sourceBrowserOpen"
      />
    </div>

    <div class="graph-body" :class="{ 'has-detail': store.selectedNode || store.selectedEdgeId }">
      <div class="graph-canvas">
        <div ref="canvasRef" class="graph-canvas-inner" />
        <div v-if="loading || rendering" class="graph-loading-badge">正在载入图谱…</div>
        <div v-else-if="initError" class="graph-empty">
          <span>{{ initError }}</span>
          <button class="btn primary" type="button" @click="retryInit">重试加载</button>
        </div>
        <div v-else-if="!store.rawNodes.length" class="graph-empty">
          <template v-if="store.sourceFocus">
            <span>来源「{{ store.sourceFocus }}」在当前密度/过滤条件下暂无可展示节点</span>
            <button class="btn" type="button" @click="exitSourceFocus">× 退出来源聚焦</button>
          </template>
          <template v-else>
            <span>当前作用域暂无图谱实体数据</span>
            <button class="btn" type="button" @click="loadAndRender">重新载入</button>
          </template>
        </div>
      </div>

      <GraphDetailPanel v-if="store.selectedNode || store.selectedEdgeId" />
    </div>

    <GraphAddNodeDialog v-model="addNodeOpen" />
    <GraphAddEdgeDialog v-model="addEdgeOpen" :default-source="store.selectedNode" />
    <GraphDictionaryDialog v-model="dictOpen" />
    <GraphSourceBrowserDialog v-model="sourceBrowserOpen" />
  </section>
</template>

<style scoped>
.graph-body {
  flex: 1 1 0;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(280px, 1fr) auto;
  gap: 12px;
  overflow: hidden;
}

@media (min-width: 980px) {
  .graph-body {
    grid-template-rows: minmax(0, 1fr);
  }
  .graph-body.has-detail {
    grid-template-columns: minmax(0, 1fr) 370px;
    grid-template-rows: minmax(0, 1fr);
  }
}

.graph-canvas {
  position: relative;
  min-width: 0;
  min-height: 280px;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

@media (min-width: 980px) {
  .graph-canvas {
    min-height: 0;
  }
}

.graph-canvas-inner {
  position: absolute;
  inset: 0;
}

.graph-empty {
  gap: 10px;
  padding: 16px;
  flex-wrap: wrap;
  text-align: center;
}

/* 修复 vis-overrides.css 中 .graph-empty { pointer-events: none } 导致重试按钮无法点击的问题 */
.graph-empty button {
  pointer-events: auto;
  position: relative;
  z-index: 3;
}
</style>
