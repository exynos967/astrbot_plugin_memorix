<script setup lang="ts">
// Graph 视图顶部工具栏：搜索定位 / 作用域切换 / 信息密度百分比 / 过滤叶子 / 载入图谱。
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

const searchKeyword = ref("");
const { currentScope, density, excludeLeaf } = storeToRefs(store);

const densityPercent = computed(() => `${Math.round((Number(density.value) || 0) * 100)}%`);

const excludeLeafModel = computed<string>({
  get: () => String(excludeLeaf.value),
  set: (v: string) => {
    excludeLeaf.value = v === "true";
  },
});

function graphNodeSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  return store.nodeLabels
    .filter((v) => !q || v.toLowerCase().includes(q))
    .slice(0, 24)
    .map((v) => ({ value: v, kind: "实体" }));
}

function locateNode(): void {
  const q = searchKeyword.value.trim().toLowerCase();
  if (!q) {
    app.pushError("请输入要定位的实体名称", "focusGraphNode");
    return;
  }
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
    app.pushError(`当前图谱中未找到节点「${searchKeyword.value.trim()}」`, "focusGraphNode");
    return;
  }
  store.selectedNode = found.id;
  store.selectedEdgeId = "";
  vis?.focusNode(found.id, 1.4);
}

function onSearchKeydown(e: KeyboardEvent): void {
  if (e.key !== "Enter") return;
  e.preventDefault();
  locateNode();
}

function onChoose(item: CandidateItem): void {
  searchKeyword.value = item.value;
  locateNode();
}

async function onScopeChange(e: Event): Promise<void> {
  const val = (e.target as HTMLSelectElement).value.trim();
  const prevEffective = store.effectiveScope();
  store.setScope(val);
  // 若 effectiveScope 未变（例如从显式默认切回空值自动解析同一 scope），手动触发一次重载；
  // 否则交由 GraphView 的 effectiveScope watcher 统一触发，避免重复请求。
  if (store.effectiveScope() === prevEffective) {
    await store.loadGraph();
  }
}

async function onReload(): Promise<void> {
  await store.loadGraph();
}
</script>

<template>
  <div class="toolbar">
    <CandidateInput
      v-model="searchKeyword"
      :source="graphNodeSource"
      :debounce-ms="160"
      placeholder="输入实体名称或 ID 回车定位"
      label="搜索节点"
      :flex="2.4"
      @choose="onChoose"
      @keydown="onSearchKeydown"
    />
    <button class="btn" type="button" @click="locateNode">定位节点</button>

    <div class="field graph-scope-field">
      <label>作用域过滤</label>
      <select
        class="select"
        :value="currentScope"
        @change="onScopeChange"
        @focus="store.loadScopes()"
      >
        <option value="">自动 / 最近</option>
        <option v-for="opt in store.scopeOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </div>

    <div class="field density-field">
      <label for="graph-density-range" class="tabular">信息密度 ({{ densityPercent }})</label>
      <input
        id="graph-density-range"
        v-model.number="density"
        class="input"
        type="range"
        min="0.1"
        max="1"
        step="0.05"
        @change="onReload"
      />
    </div>

    <label class="field leaf-field">
      <span>过滤孤立叶节点</span>
      <select v-model="excludeLeafModel" class="select" @change="onReload">
        <option value="true">开启</option>
        <option value="false">关闭</option>
      </select>
    </label>

    <button
      class="btn primary"
      type="button"
      :disabled="store.loading"
      @click="store.loadGraph()"
    >
      {{ store.loading ? "载入中…" : "载入图谱" }}
    </button>
  </div>
</template>

<style scoped>
.density-field {
  min-width: 140px;
}

.leaf-field {
  max-width: 140px;
}

@media (max-width: 640px) {
  .leaf-field {
    max-width: none;
  }
}
</style>
