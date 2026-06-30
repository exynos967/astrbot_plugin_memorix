<script setup lang="ts">
// Graph 视图顶部工具栏（从 legacy view-graph toolbar 迁移）。
// 职责：搜索定位 / 群过滤 / 信息密度 / 过滤叶子 / 载入图谱 / 整理布局 / zoom 控件。
// 数据流：scope/density/excludeLeaf/zoom 通过 storeToRefs 取 ref 给 v-model 双向绑定；
// vis 动作通过 inject(GRAPH_VIS_KEY) 调用，调用前判空（GraphView 未 provide 完成时为 null）。
import { computed, inject, ref } from "vue";
import { storeToRefs } from "pinia";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { GRAPH_VIS_KEY, type VisController } from "@/composables/useVisNetwork";
import CandidateInput from "@/components/common/CandidateInput.vue";
import type { CandidateItem } from "@/stores/candidate";

const store = useGraphStore();
const app = useAppStore();
const vis = inject<VisController | null>(GRAPH_VIS_KEY, null);

// 搜索关键字为本地 ref（不进 store，仅本组件用）
const searchKeyword = ref("");

// storeToRefs 取 ref 供 v-model 双向绑定（setup store 自动解包，但 v-model 需 Ref）
// zoom 仍解构：供缩放百分比展示用（滑块已删，缩放改由 +/- 按钮控制）。
const { currentScope, density, excludeLeaf, zoom } = storeToRefs(store);

// excludeLeaf 是 boolean，<select> 用 "true"/"false" 字符串，computed 做转换
const excludeLeafModel = computed<string>({
  get: () => String(excludeLeaf.value),
  set: (v: string) => {
    excludeLeaf.value = v === "true";
  },
});

/**
 * 候选源：从 nodeLabels 过滤出匹配项（与 spec graphNodeSource 一致）。
 * 选择候选后立即触发定位，复刻 legacy datalist 选中即 focus 的体验。
 */
function graphNodeSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  return store.nodeLabels
    .filter((v) => !q || v.toLowerCase().includes(q))
    .slice(0, 10)
    .map((v) => ({ value: v, kind: "实体" }));
}

/**
 * 定位节点：rawNodes 中 label/id 先精确后包含匹配（与 legacy focusGraphNode 一致）。
 * 找到后调 vis.focusNode(node.id, 1.4)（focusNode 接收节点 id）；找不到 pushError "未找到节点"。
 */
function locateNode(): void {
  const q = searchKeyword.value.trim().toLowerCase();
  if (!q) return;
  const found =
    store.rawNodes.find((n) => {
      const label = String(n.label || "").toLowerCase();
      const id = String(n.id || "").toLowerCase();
      return label === q || id === q;
    }) ||
    store.rawNodes.find((n) => {
      const label = String(n.label || "").toLowerCase();
      const id = String(n.id || "").toLowerCase();
      return label.includes(q) || id.includes(q);
    });
  if (!found) {
    app.pushError("未找到节点", "focusGraphNode");
    return;
  }
  vis?.focusNode(found.id, 1.4);
}

// 回车触发定位（CandidateInput 内部候选菜单已打开时不拦截 Enter，这里在 keydown 捕获）
function onSearchKeydown(e: KeyboardEvent): void {
  if (e.key !== "Enter") return;
  e.preventDefault();
  locateNode();
}

// 候选菜单选中某项 → 立即定位
function onChoose(item: CandidateItem): void {
  searchKeyword.value = item.value;
  locateNode();
}

// 群过滤 change：setScope 后 loadGraph
async function onScopeChange(e: Event): Promise<void> {
  const val = (e.target as HTMLSelectElement).value.trim();
  store.setScope(val);
  await store.loadGraph();
}

// 适配视图按钮：fit + 清除用户缩放标记
function onFit(): void {
  vis?.fitGraphView(true);
  store.userZoomed = false;
}

// 整理布局：legacy 用 network.stabilize(120)，但 vis 未暴露 stabilize，
// 改为重新适配视图（重新 fit），让布局稳定展示。
function onLayout(): void {
  vis?.fitGraphView(true);
}
</script>

<template>
  <div class="toolbar">
    <!-- 搜索节点：CandidateInput + 定位 -->
    <CandidateInput
      v-model="searchKeyword"
      :source="graphNodeSource"
      :debounce-ms="180"
      placeholder="输入实体名称"
      label="搜索节点"
      @choose="onChoose"
      @keydown="onSearchKeydown"
    />
    <button class="btn" @click="locateNode">定位节点</button>

    <!-- 群过滤：select 绑定 currentScope，options 含空值 + scopeOptions -->
    <div class="field graph-scope-field">
      <label>群过滤</label>
      <select class="select" :value="currentScope" @change="onScopeChange" @focus="store.loadScopes()">
        <option value="">自动 / 最近</option>
        <option v-for="opt in store.scopeOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </div>

    <!-- 信息密度：range -->
    <div class="field">
      <label>信息密度</label>
      <input class="input" type="range" min="0.1" max="1" step="0.05" v-model.number="density" />
    </div>

    <!-- 过滤叶子：select 字符串转布尔 -->
    <label class="field" style="max-width: 130px">
      <span>过滤叶子</span>
      <select class="select" v-model="excludeLeafModel">
        <option value="true">开启</option>
        <option value="false">关闭</option>
      </select>
    </label>

    <!-- 载入图谱 -->
    <button class="btn primary" :disabled="store.loading" @click="store.loadGraph()">载入图谱</button>

    <!-- 整理布局：vis 未暴露 stabilize，改用 fitGraphView 重新适配 -->
    <button class="btn" @click="onLayout">整理布局</button>

    <!-- zoom 控件：滑块因 .input padding 导致拖不到底且与按钮重复，已移除；
         缩放由 - / + 按钮 + 适配视图 + 百分比显示承担。 -->
    <div class="graph-zoom" aria-label="图谱缩放">
      <button class="btn icon" title="缩小图谱" @click="vis?.adjustZoom(-0.15)">-</button>
      <button class="btn icon" title="放大图谱" @click="vis?.adjustZoom(0.15)">+</button>
      <button class="btn icon" title="适配视图" @click="onFit">⤢</button>
      <span class="zoom-value">{{ Math.round(zoom * 100) }}%</span>
    </div>
  </div>
</template>
