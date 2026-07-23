<template>
  <div class="band">
    <!-- 顶部标题 + 清除按钮 -->
    <div class="panel-title">
      <h2>{{ panelTitle }}</h2>
      <div class="toolbar">
        <button class="btn icon" title="清除选中" @click="clearSelection">×</button>
      </div>
    </div>

    <!-- 节点详情 -->
    <div v-if="selectedNode" class="detail-body">
      <div class="field">
        <label>重命名</label>
        <input v-model="newName" class="input" placeholder="新节点名称" />
      </div>
      <div class="toolbar" style="margin-top: 10px">
        <button class="btn primary" @click="onRename">重命名</button>
        <button class="btn danger" @click="onRemoveNode">删除节点</button>
        <button class="btn" @click="onHighlight">高亮邻域</button>
        <button class="btn" @click="onFocusNode">居中查看</button>
      </div>

      <!-- 邻域拓扑 -->
      <div style="margin-top: 14px">
        <div v-if="!neighborEdges.length" class="empty">暂无相邻关系</div>
        <div v-else class="result compact">
          <div class="result-head">
            <h3>邻域拓扑</h3>
            <span class="tag">{{ neighborEdges.length }} 条关系</span>
          </div>
          <div
            v-for="item in neighborEdges"
            :key="item.edgeId"
            class="topology-row"
          >
            <div>
              <strong>{{ item.direction }} {{ item.other }}</strong>
              <span class="cell-note">{{ item.label }}</span>
            </div>
            <div class="result-actions" style="margin-top: 0">
              <button class="btn" @click="vis?.focusNode(item.other)">跳转</button>
              <button class="btn" @click="store.selectedEdgeId = item.edgeId">关系</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 来源预览 -->
      <div class="result-list" style="margin-top: 14px">
        <div v-if="sourceLoading" class="empty">正在加载来源</div>
        <div v-else-if="!sources.length" class="empty">没有来源段落</div>
        <div v-for="(item, idx) in sources" :key="item.hash || idx" class="result">
          <div class="result-head">
            <h3>{{ sourceName(item) || item.hash || "source" }}</h3>
          </div>
          <p v-if="item.content">{{ truncate(item.content) }}</p>
          <div class="tags">
            <span v-if="item.hash" class="tag mono">{{ String(item.hash).slice(0, 16) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 边详情 -->
    <div v-else-if="currentEdge" class="detail-body">
      <div class="result">
        <h3>{{ currentEdge.label || "关系" }}</h3>
        <p>{{ currentEdge.from }} → {{ currentEdge.to }}</p>
        <div class="tags">
          <span
            v-for="(p, i) in edgePredicateTags"
            :key="i"
            class="tag"
          >{{ p }}</span>
          <span v-if="currentEdge.is_pinned" class="tag warn">置顶</span>
          <span v-if="currentEdge.is_protected" class="tag ok">保护中</span>
        </div>
      </div>
      <div class="toolbar" style="margin-top: 12px">
        <button class="btn danger" @click="onRemoveEdge">删除关系</button>
        <button class="btn" @click="vis?.focusNode(currentEdge.from)">居中查看</button>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="empty">未选择节点或关系</div>
  </div>
</template>

<style scoped>
/* 作为 .graph-body grid 列子项：独立纵向滚动，min-height:0 允许收缩不撑破父容器。
 * 根 .band 默认 overflow:hidden 会裁内容，这里覆写为 auto 让详情可滚。 */
.band {
  min-height: 0;
  overflow: auto;
}
</style>

<script setup lang="ts">
import { ref, computed, watch, inject } from "vue";
import { storeToRefs } from "pinia";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { GRAPH_VIS_KEY, type VisController } from "@/composables/useVisNetwork";
import { fetchSourceList, type SourceParagraphItem } from "@/services/sourceApi";
import type { GraphEdge } from "@/services/graphApi";
import { errText } from "@/utils/error";

const store = useGraphStore();
const app = useAppStore();
const vis = inject<VisController | null>(GRAPH_VIS_KEY, null);

const { selectedNode, selectedEdgeId, rawEdges, zoom } = storeToRefs(store);

// 本地重命名输入
const newName = ref("");

// 来源预览状态
const sources = ref<SourceParagraphItem[]>([]);
const sourceLoading = ref(false);

// 顶部标题
const panelTitle = computed(() => {
  if (selectedNode.value) return `节点：${selectedNode.value}`;
  if (currentEdge.value) return `关系：${currentEdge.value.from} → ${currentEdge.value.to}`;
  return "详情";
});

// 当前选中的边（从 rawEdges 派生：id 匹配 或 from_to 匹配）
const currentEdge = computed<GraphEdge | null>(() => {
  const id = selectedEdgeId.value;
  if (!id) return null;
  return (
    rawEdges.value.find((e) => e.id === id) ||
    rawEdges.value.find((e) => `${e.from}_${e.to}` === id) ||
    null
  );
});

// 边谓词标签（前 6 个）
const edgePredicateTags = computed(() => {
  const ps = currentEdge.value?.predicates || [];
  return ps.slice(0, 6);
});

// 邻域拓扑（与 legacy renderNodeRelations 一致）
interface NeighborItem {
  edgeId: string;
  direction: string;
  other: string;
  label: string;
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
      };
    });
});

// 来源名取值（与 legacy sourceNameOf 对齐）
function sourceName(item: SourceParagraphItem): string {
  return item.source || "";
}

// content 截断
function truncate(text: string): string {
  const max = 200;
  return text.length > max ? text.slice(0, max) + "…" : text;
}

// 清除选中
function clearSelection(): void {
  store.selectedNode = "";
  store.selectedEdgeId = "";
}

// 重命名
async function onRename(): Promise<void> {
  const oldId = selectedNode.value;
  const newId = newName.value.trim();
  if (!oldId || !newId || oldId === newId) return;
  const ok = await store.renameNode(oldId, newId);
  if (ok) {
    await store.loadGraph();
    clearSelection();
  }
}

// 删除节点
async function onRemoveNode(): Promise<void> {
  const id = selectedNode.value;
  if (!id) return;
  const confirmed = await app.requestConfirmation({
    title: "删除节点",
    message: `确定删除节点 ${id}？相关图谱关系也可能受到影响。`,
    confirmText: "删除",
    danger: true,
  });
  if (!confirmed) return;
  const ok = await store.removeNode(id);
  if (ok) {
    await store.loadGraph();
    clearSelection();
  }
}

// 高亮邻域
function onHighlight(): void {
  const id = selectedNode.value;
  if (id) vis?.highlightNeighborhood(id);
}

// 居中查看节点
function onFocusNode(): void {
  const id = selectedNode.value;
  if (id) vis?.focusNode(id, Math.max(1.15, zoom.value));
}

// 删除关系
async function onRemoveEdge(): Promise<void> {
  const edge = currentEdge.value;
  if (!edge) return;
  const confirmed = await app.requestConfirmation({
    title: "删除关系",
    message: `确定删除关系 ${edge.from} → ${edge.to}？`,
    confirmText: "删除",
    danger: true,
  });
  if (!confirmed) return;
  const ok = await store.removeEdge(edge.from, edge.to);
  if (ok) {
    await store.loadGraph();
    clearSelection();
  }
}

// 加载来源预览（节点选中时）。请求序号守卫：快速切换节点时丢弃过期请求，避免旧来源覆盖新选中。
let sourceSeq = 0;
async function loadNodeSources(nodeId: string): Promise<void> {
  if (!nodeId) {
    sources.value = [];
    return;
  }
  const seq = ++sourceSeq;
  sourceLoading.value = true;
  try {
    const data = await fetchSourceList({ node_id: nodeId });
    if (seq !== sourceSeq) return; // 已有更新的选中节点，丢弃本次结果
    sources.value = (data.sources || []) as SourceParagraphItem[];
  } catch (err) {
    if (seq !== sourceSeq) return;
    app.pushError(errText(err), "loadNodeSources");
    sources.value = [];
  } finally {
    if (seq === sourceSeq) sourceLoading.value = false;
  }
}

// 选中节点变化时：同步重命名输入 + 拉取来源
watch(
  selectedNode,
  (id) => {
    newName.value = id || "";
    if (id) void loadNodeSources(id);
    else sources.value = [];
  },
  { immediate: true },
);
</script>
