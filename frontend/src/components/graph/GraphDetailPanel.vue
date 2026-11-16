<script setup lang="ts">
// Graph 右侧/底部详情面板：扁平 1px 细线分区（<=2 层盒嵌套），支持实体重命名/删除/邻域跳转与关系权重编辑/证据溯源。
import { computed, inject, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { GRAPH_VIS_KEY, type VisController } from "@/composables/useVisNetwork";
import { fetchSourceList, type SourceParagraphItem } from "@/services/sourceApi";
import type { GraphEdge } from "@/services/graphApi";
import { formatEdgeWeight } from "@/utils/graphText";
import { formatTs } from "@/utils/time";
import { errText } from "@/utils/error";

const store = useGraphStore();
const app = useAppStore();
const vis = inject<VisController | null>(GRAPH_VIS_KEY, null);

const { selectedNode, selectedEdgeId, rawEdges, zoom } = storeToRefs(store);

const newName = ref("");
const busy = ref(false);

// 节点关联来源段落
const sources = ref<SourceParagraphItem[]>([]);
const sourceLoading = ref(false);

// 边详情：权重编辑 + 证据段落
const edgeWeightInput = ref<number>(1);
const edgeSources = ref<SourceParagraphItem[]>([]);
const edgeSourceLoading = ref(false);

const currentEdge = computed<GraphEdge | null>(() => {
  const id = selectedEdgeId.value;
  if (!id) return null;
  return (
    rawEdges.value.find((e) => e.id === id) ||
    rawEdges.value.find((e) => `${e.from}_${e.to}` === id) ||
    null
  );
});

const panelTitle = computed(() => {
  if (selectedNode.value) return `实体：${selectedNode.value}`;
  if (currentEdge.value) return `关系：${currentEdge.value.from} → ${currentEdge.value.to}`;
  return "图谱详情";
});

const edgePredicateTags = computed(() => {
  const ps = currentEdge.value?.predicates || [];
  return ps.slice(0, 8);
});

interface NeighborItem {
  edgeId: string;
  direction: string;
  other: string;
  label: string;
  weightText: string;
}

const neighborEdges = computed<NeighborItem[]>(() => {
  const id = selectedNode.value;
  if (!id) return [];
  return rawEdges.value
    .filter((e) => e.from === id || e.to === id)
    .map((e) => {
      const outgoing = e.from === id;
      return {
        edgeId: e.id || `${e.from}_${e.to}`,
        direction: outgoing ? "→" : "←",
        other: outgoing ? e.to : e.from,
        label: e.label || (e.predicates || [])[0] || "关联",
        weightText: formatEdgeWeight(e.value),
      };
    });
});

function sourceName(item: SourceParagraphItem): string {
  return item.source || "默认来源";
}

function truncate(text: string, max = 220): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function clearSelection(): void {
  store.selectedNode = "";
  store.selectedEdgeId = "";
}

function jumpToNode(nodeId: string): void {
  if (!nodeId) return;
  store.selectedNode = nodeId;
  store.selectedEdgeId = "";
  vis?.focusNode(nodeId, Math.max(1.25, zoom.value));
}

function switchToEdge(edgeId: string): void {
  if (!edgeId) return;
  store.selectedNode = "";
  store.selectedEdgeId = edgeId;
}

async function onRename(): Promise<void> {
  if (busy.value) return;
  const oldId = selectedNode.value;
  const newId = newName.value.trim();
  if (!oldId || !newId || oldId === newId) return;
  busy.value = true;
  try {
    const ok = await store.renameNode(oldId, newId);
    if (ok) {
      app.pushSuccess?.(`节点已重命名为「${newId}」`, "renameNode");
      await store.loadGraph();
      store.selectedNode = newId;
    }
  } finally {
    busy.value = false;
  }
}

async function onRemoveNode(): Promise<void> {
  if (busy.value) return;
  const id = selectedNode.value;
  if (!id) return;
  const confirmed = await app.requestConfirmation({
    title: "删除实体节点",
    message: `确定删除实体「${id}」？与其相连的图谱关系也将受到影响。`,
    confirmText: "确认删除",
    danger: true,
  });
  if (!confirmed) return;
  busy.value = true;
  try {
    const ok = await store.removeNode(id);
    if (ok) {
      app.pushSuccess?.(`实体「${id}」已删除`, "deleteNode");
      await store.loadGraph();
      clearSelection();
    }
  } finally {
    busy.value = false;
  }
}

function onHighlight(): void {
  const id = selectedNode.value;
  if (id) vis?.highlightNeighborhood(id);
}

function onFocusNode(): void {
  const id = selectedNode.value;
  if (id) vis?.focusNode(id, Math.max(1.2, zoom.value));
}

async function onUpdateEdgeWeight(): Promise<void> {
  if (busy.value) return;
  const edge = currentEdge.value;
  if (!edge) return;
  const w = Number(edgeWeightInput.value);
  if (!Number.isFinite(w) || w <= 0) {
    app.pushError("关系权重必须为大于 0 的数值", "updateEdgeWeight");
    return;
  }
  const keepEdgeId = edge.id || `${edge.from}_${edge.to}`;
  busy.value = true;
  try {
    const ok = await store.setEdgeWeight(edge.from, edge.to, w);
    if (ok) {
      app.pushSuccess?.(`关系权重已更新为 ${w.toFixed(2)}`, "updateEdgeWeight");
      await store.loadGraph();
      store.selectedNode = "";
      store.selectedEdgeId = keepEdgeId;
    }
  } finally {
    busy.value = false;
  }
}

async function onRemoveEdge(): Promise<void> {
  if (busy.value) return;
  const edge = currentEdge.value;
  if (!edge) return;
  const confirmed = await app.requestConfirmation({
    title: "删除记忆关系",
    message: `确定删除关系「${edge.from} → ${edge.to}」？`,
    confirmText: "确认删除",
    danger: true,
  });
  if (!confirmed) return;
  busy.value = true;
  try {
    const ok = await store.removeEdge(edge.from, edge.to);
    if (ok) {
      app.pushSuccess?.("关系已删除", "deleteEdge");
      await store.loadGraph();
      clearSelection();
    }
  } finally {
    busy.value = false;
  }
}

let nodeSourceSeq = 0;
async function loadNodeSources(nodeId: string): Promise<void> {
  if (!nodeId) {
    sources.value = [];
    return;
  }
  const seq = ++nodeSourceSeq;
  sourceLoading.value = true;
  try {
    const data = await fetchSourceList({ node_id: nodeId }, store.effectiveScope());
    if (seq !== nodeSourceSeq) return;
    sources.value = (data.sources || []) as SourceParagraphItem[];
  } catch (err) {
    if (seq !== nodeSourceSeq) return;
    app.pushError(errText(err), "loadNodeSources");
    sources.value = [];
  } finally {
    if (seq === nodeSourceSeq) sourceLoading.value = false;
  }
}

let edgeSourceSeq = 0;
async function loadEdgeSources(edge: GraphEdge | null): Promise<void> {
  if (!edge) {
    edgeSources.value = [];
    return;
  }
  const seq = ++edgeSourceSeq;
  edgeSourceLoading.value = true;
  try {
    const data = await fetchSourceList(
      { edge_source: edge.from, edge_target: edge.to },
      store.effectiveScope(),
    );
    if (seq !== edgeSourceSeq) return;
    edgeSources.value = (data.sources || []) as SourceParagraphItem[];
  } catch (err) {
    if (seq !== edgeSourceSeq) return;
    app.pushError(errText(err), "loadEdgeSources");
    edgeSources.value = [];
  } finally {
    if (seq === edgeSourceSeq) edgeSourceLoading.value = false;
  }
}

watch(
  selectedNode,
  (id) => {
    newName.value = id || "";
    if (id) void loadNodeSources(id);
    else sources.value = [];
  },
  { immediate: true },
);

watch(
  currentEdge,
  (edge) => {
    if (edge) {
      const raw = edge.value != null && Number.isFinite(Number(edge.value)) ? Number(edge.value) : 1;
      edgeWeightInput.value = Number(raw.toFixed(2));
      void loadEdgeSources(edge);
    } else {
      edgeSources.value = [];
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="band detail-panel">
    <div class="panel-title">
      <h2>{{ panelTitle }}</h2>
      <button
        class="btn ghost sm"
        type="button"
        title="关闭详情面板"
        aria-label="关闭详情面板"
        @click="clearSelection"
      >
        关闭
      </button>
    </div>

    <!-- 节点详情 -->
    <div v-if="selectedNode" class="detail-body">
      <div class="field">
        <label for="graph-detail-rename">重命名实体</label>
        <input
          id="graph-detail-rename"
          v-model="newName"
          class="input"
          placeholder="输入新实体名称后按回车保存"
          @keyup.enter="onRename"
        />
      </div>
      <div class="detail-actions">
        <button class="btn primary sm" type="button" :disabled="busy" @click="onRename">
          重命名节点
        </button>
        <button class="btn sm" type="button" @click="onHighlight">高亮邻域</button>
        <button class="btn sm" type="button" @click="onFocusNode">居中查看</button>
        <button class="btn danger sm" type="button" :disabled="busy" @click="onRemoveNode">
          删除节点
        </button>
      </div>

      <div class="detail-section">
        <div class="detail-section-head">
          <h3>邻域拓扑</h3>
          <span class="tag tabular">{{ neighborEdges.length }} 条关系</span>
        </div>
        <div v-if="!neighborEdges.length" class="empty">暂无相邻关系边</div>
        <div v-else class="rows">
          <div v-for="item in neighborEdges" :key="item.edgeId" class="topology-row">
            <div class="topo-main">
              <strong>{{ item.direction }} {{ item.other }}</strong>
              <span class="cell-note tabular">{{ item.label }} · 权重 {{ item.weightText }}</span>
            </div>
            <div class="topo-btns">
              <button class="btn sm" type="button" @click="jumpToNode(item.other)">定位</button>
              <button class="btn sm" type="button" @click="switchToEdge(item.edgeId)">查看边</button>
            </div>
          </div>
        </div>
      </div>

      <div class="detail-section">
        <div class="detail-section-head">
          <h3>关联证据段落</h3>
          <span class="tag tabular">{{ sources.length }} 条</span>
        </div>
        <div v-if="sourceLoading" class="empty">正在检索关联来源段落…</div>
        <div v-else-if="!sources.length" class="empty">当前作用域下暂无关联来源段落</div>
        <div v-else class="rows">
          <div v-for="(item, idx) in sources" :key="item.hash || idx" class="evidence-row">
            <div class="evidence-head">
              <strong>{{ sourceName(item) }}</strong>
              <span v-if="item.created_at" class="tag tabular">{{ formatTs(item.created_at) }}</span>
            </div>
            <p v-if="item.content" class="evidence-text">{{ truncate(item.content) }}</p>
            <div v-if="item.hash" class="tags">
              <span class="tag mono">哈希 {{ String(item.hash).slice(0, 12) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 边详情 -->
    <div v-else-if="currentEdge" class="detail-body">
      <div class="edge-summary">
        <div class="evidence-head">
          <strong>{{ currentEdge.label || "记忆关系" }}</strong>
          <span class="tag mono tabular">权重 {{ formatEdgeWeight(currentEdge.value) }}</span>
        </div>
        <p class="edge-pair">{{ currentEdge.from }} → {{ currentEdge.to }}</p>
        <div class="tags">
          <span v-for="(p, i) in edgePredicateTags" :key="i" class="tag">{{ p }}</span>
          <span v-if="currentEdge.is_pinned" class="tag warn">永久置顶</span>
          <span v-if="currentEdge.is_protected" class="tag ok">限时保护</span>
          <span v-if="currentEdge.is_active === false" class="tag bad">冷冻状态</span>
        </div>
      </div>

      <div class="edge-weight-box">
        <label class="field" style="flex: 1">
          <span>关系权重</span>
          <input
            v-model.number="edgeWeightInput"
            class="input tabular"
            type="number"
            min="0.05"
            step="0.05"
            @keyup.enter="onUpdateEdgeWeight"
          />
        </label>
        <button
          class="btn primary"
          type="button"
          :disabled="busy"
          @click="onUpdateEdgeWeight"
        >
          保存权重
        </button>
      </div>

      <div class="detail-actions">
        <button class="btn sm" type="button" @click="jumpToNode(currentEdge.from)">
          定位起点
        </button>
        <button class="btn sm" type="button" @click="jumpToNode(currentEdge.to)">
          定位终点
        </button>
        <button class="btn danger sm" type="button" :disabled="busy" @click="onRemoveEdge">
          删除关系
        </button>
      </div>

      <div class="detail-section">
        <div class="detail-section-head">
          <h3>关系证据段落</h3>
          <span class="tag tabular">{{ edgeSources.length }} 条</span>
        </div>
        <div v-if="edgeSourceLoading" class="empty">正在查询支撑该关系的原文证据…</div>
        <div v-else-if="!edgeSources.length" class="empty">暂无直接支撑该关系边的段落记录</div>
        <div v-else class="rows">
          <div v-for="(item, idx) in edgeSources" :key="item.hash || idx" class="evidence-row">
            <div class="evidence-head">
              <strong>{{ sourceName(item) }}</strong>
              <span v-if="item.created_at" class="tag tabular">{{ formatTs(item.created_at) }}</span>
            </div>
            <p v-if="item.content" class="evidence-text">{{ truncate(item.content) }}</p>
            <div v-if="item.hash" class="tags">
              <span class="tag mono">哈希 {{ String(item.hash).slice(0, 12) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="empty">点击画布节点或关系边查看详情</div>
  </div>
</template>

<style scoped>
.detail-panel {
  margin-top: 0;
  min-height: 0;
  overflow-y: auto;
}

@media (max-width: 979px) {
  .detail-panel {
    max-height: 42vh;
    overflow-y: auto;
  }
}

.detail-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail-section {
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.detail-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.detail-section-head h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 650;
  color: var(--ink);
}

.topo-main {
  min-width: 0;
}

.topo-main strong {
  display: block;
  font-size: 13px;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.topo-btns {
  display: flex;
  gap: 6px;
}

.edge-summary {
  padding-bottom: 12px;
  border-bottom: 1px solid var(--hairline);
}

.edge-pair {
  margin: 6px 0 0;
  font-size: 13.5px;
  color: var(--text);
  overflow-wrap: anywhere;
}

.edge-weight-box {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.evidence-row {
  padding: 10px 0;
}

.evidence-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.evidence-head strong {
  font-size: 13px;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.evidence-text {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--muted);
  overflow-wrap: anywhere;
}
</style>
