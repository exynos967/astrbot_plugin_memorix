<template>
  <!-- graph 视图第二行工具按钮 + filter chip：本组件只触发动作，弹窗开关由 GraphView 持有 -->
  <div class="graph-tools">
    <button class="btn" @click="emit('update:addNodeOpen', true)">新增节点</button>
    <button class="btn" @click="emit('update:addEdgeOpen', true)">新增关系</button>
    <button class="btn" @click="emit('update:dictOpen', true)">内容字典</button>
    <button class="btn" @click="emit('update:sourceBrowserOpen', true)">来源批次</button>
    <button class="btn" @click="clearSource">全部图谱</button>
    <button class="btn" :class="{ active: !simulationRunning }" @click="toggleSim">
      {{ simulationRunning ? "暂停模拟" : "继续布局" }}
    </button>
    <button class="btn" :class="{ active: lowPerf }" @click="togglePerf">性能模式</button>

    <span class="tag" :class="{ ok: !!(sourceFocus || currentScope) }">{{ filterChipText }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed, inject } from "vue";
import { storeToRefs } from "pinia";
import { useGraphStore } from "@/stores/graph";
import { GRAPH_VIS_KEY, type VisController } from "@/composables/useVisNetwork";
import { scopeLabel } from "@/utils/graphText";

// 弹窗打开状态由 GraphView 持有，本组件仅通过 v-model 式 emit 通知打开
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

const { sourceFocus, currentScope, resolvedScope, scopeOptions, simulationRunning, lowPerf } =
  storeToRefs(store);

// filter chip 文案与 legacy updateGraphToolState 一致
const filterChipText = computed(() => {
  if (sourceFocus.value) return `source: ${sourceFocus.value}`;
  if (currentScope.value) return `scope: ${scopeLabel(currentScope.value, scopeOptions.value)}`;
  // 无显式 scope：解析后的 scope 显示「（自动）」后缀，否则提示自动 scope
  return resolvedScope.value
    ? `scope: ${scopeLabel(resolvedScope.value, scopeOptions.value)}（自动）`
    : "自动 scope";
});

// 全部图谱：清空来源过滤后重新加载
function clearSource() {
  store.sourceFocus = "";
  store.loadGraph();
}

function toggleSim() {
  vis?.toggleSimulation();
}

function togglePerf() {
  vis?.applyLowPerf();
}

</script>
