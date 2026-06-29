<script setup lang="ts">
// Graph 视图主协调器：vis-network 画布 + 工具栏 + 详情面板 + 各类弹窗。
// 从 legacy view-graph（index.html 行 2104-2156）迁移，整合 useVisNetwork + 子组件。
//
// 修复点（H5/C1）：
// - H5：legacy setView graph 用 setTimeout(80) 等 canvas 尺寸 → 改 nextTick + ResizeObserver，
//   canvas 有尺寸后再 renderGraph，杜绝未布局就渲染。
// - C1：store.loadGraph 错误进 store.initError，本视图显示空状态 + 重试按钮。
// - vis 实例唯一，通过 provide(GRAPH_VIS_KEY) 下发子组件，子组件 inject 访问。
// - 消除 window.*：showNodeDetail/showEdgeDetail 改为写 store.selectedNode/selectedEdgeId。
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

// 本地渲染标志：精确覆盖「数据已到但画布尚未画出」的中间态。
// store.loading 仅覆盖网络请求阶段；rendering 覆盖 nextTick+RAF 的渲染间隙。
// 两者任一为真 → 加载指示器显示。渲染成功后清 rendering，杜绝"空画布无提示"。
const rendering = ref(false);

// 唯一 vis 控制器：节点/边点击写 store，由 GraphDetailPanel 响应
const vis = useVisNetwork({
  onNodeSelect: (id) => {
    store.selectedNode = id;
    store.selectedEdgeId = "";
  },
  onEdgeSelect: (id) => {
    store.selectedEdgeId = id;
    store.selectedNode = "";
  },
});
provide(GRAPH_VIS_KEY, vis);

// 弹窗显隐（GraphTools 通过 v-model:xxxOpen 协调）
const addNodeOpen = ref(false);
const addEdgeOpen = ref(false);
const dictOpen = ref(false);
const sourceBrowserOpen = ref(false);

/** H5：canvas 有尺寸后渲染图谱（替代 legacy setTimeout 80ms）。 */
function renderWhenReady(): void {
  const el = canvasRef.value;
  if (!el) return;
  rendering.value = true;
  nextTick(() => {
    // 双保险：nextTick 后再用一帧确保布局完成
    window.requestAnimationFrame(() => {
      if (canvasRef.value && canvasRef.value.clientWidth > 0) {
        try {
          vis.renderGraph(canvasRef.value);
          // 渲染成功：清本地标志（store.loading 此时已为 false）
          rendering.value = false;
        } catch (err) {
          rendering.value = false;
          store.initError = err instanceof Error ? err.message : String(err);
        }
      } else {
        // canvas 仍无尺寸：保留 rendering，等 ResizeObserver 补渲染后清
        rendering.value = false;
      }
    });
  });
}

/** 载入图谱数据并渲染。 */
async function loadAndRender(): Promise<void> {
  await store.loadGraph();
  if (!initError.value) renderWhenReady();
}

onMounted(() => {
  store.loadScopes();
  // H5：ResizeObserver 确保 canvas 首次有尺寸时触发渲染
  const el = canvasRef.value;
  if (el && typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => {
      // 仅在已有数据但 network 未就绪时补渲染一次
      if (store.rawNodes.length && !vis.ready.value) renderWhenReady();
    });
    resizeObserver.observe(el);
  }
  void loadAndRender();
});

// store.rawNodes 变化（loadGraph 成功 / scope 切换 / CRUD 刷新）后重新渲染
watch(
  () => store.rawNodes,
  () => {
    if (!initError.value) renderWhenReady();
  },
);

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
  // vis.destroy 由 useVisNetwork 的 onBeforeUnmount 处理
});

function retryInit(): void {
  if (canvasRef.value) {
    store.initError = "";
    void loadAndRender();
  }
}
</script>

<template>
  <section class="view-graph">
    <!-- 顶部工具栏区（auto 高度，内容多时内部 wrap 换行） -->
    <div class="band" style="margin-top: 0">
      <GraphToolbar />
      <GraphTools
        v-model:addNodeOpen="addNodeOpen"
        v-model:addEdgeOpen="addEdgeOpen"
        v-model:dictOpen="dictOpen"
        v-model:sourceBrowserOpen="sourceBrowserOpen"
      />
    </div>

    <!-- 画布 + 详情面板：宽屏并排、窄屏堆叠，详情面板独立滚动绝不挤到视口外 -->
    <div class="graph-body" :class="{ 'has-detail': store.selectedNode || store.selectedEdgeId }">
      <div class="graph-canvas">
        <div ref="canvasRef" class="graph-canvas-inner" />
        <!-- 加载态：仅角落小指示器，绝不覆盖已渲染的画布（修残留"载入中"盖住图谱） -->
        <div v-if="loading || rendering" class="graph-loading-badge">载入中…</div>
        <!-- 错误态：居中提示 + 重试 -->
        <div v-else-if="initError" class="graph-empty">
          <span>{{ initError }}</span>
          <button class="btn" style="margin-left: 10px" @click="retryInit">重试</button>
        </div>
        <!-- 空数据态：仅在非加载且真无数据时居中提示 -->
        <div v-else-if="!store.rawNodes.length" class="graph-empty">暂无图谱数据，点"载入图谱"</div>
      </div>

      <GraphDetailPanel v-if="store.selectedNode || store.selectedEdgeId" />
    </div>

    <GraphAddNodeDialog v-model="addNodeOpen" />
    <GraphAddEdgeDialog v-model="addEdgeOpen" />
    <GraphDictionaryDialog v-model="dictOpen" />
    <GraphSourceBrowserDialog v-model="sourceBrowserOpen" />
  </section>
</template>

<style scoped>
/* 画布+详情主区：宽屏左右并排（画布自适应、详情定宽可滚），窄屏堆叠。
 * grid 让画布吃剩余空间，详情面板高度受限于主区、内部 overflow:auto 独立滚动，
 * 杜绝详情面板被挤到视口外被浏览器截断。 */
.graph-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) auto;
  gap: 12px;
}

@media (min-width: 980px) {
  .graph-body.has-detail {
    grid-template-columns: minmax(0, 1fr) 360px;
    grid-template-rows: minmax(0, 1fr);
  }
}

.graph-canvas {
  position: relative;
  min-height: 0;
  width: 100%;
  height: 100%;
  min-height: 320px;
}

.graph-canvas-inner {
  position: absolute;
  inset: 0;
}
</style>
