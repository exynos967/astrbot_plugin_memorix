<script setup lang="ts">
// 内容字典弹窗：扁平 1px 细线行列表展示当前图谱实体与关系，保留 0.05 冷冻边权重，定位时同步详情面板。
import { computed, inject, onBeforeUnmount, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { GRAPH_VIS_KEY, type VisController } from "@/composables/useVisNetwork";
import type { GraphEdge } from "@/services/graphApi";
import { formatEdgeWeight } from "@/utils/graphText";

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const app = useAppStore();
const store = useGraphStore();
const { rawNodes, rawEdges } = storeToRefs(store);
const vis = inject<VisController | null>(GRAPH_VIS_KEY, null);

const tab = ref<"nodes" | "edges">("nodes");
const filter = ref("");

const filteredNodes = computed(() => {
  const q = filter.value.trim().toLowerCase();
  return rawNodes.value
    .filter((node) => [node.id, node.label].join(" ").toLowerCase().includes(q))
    .slice(0, 160);
});

const filteredEdges = computed(() => {
  const q = filter.value.trim().toLowerCase();
  return rawEdges.value
    .filter((edge) =>
      [edge.from, edge.to, edge.label, ...(edge.predicates || [])]
        .join(" ")
        .toLowerCase()
        .includes(q),
    )
    .slice(0, 160);
});

function close(): void {
  emit("update:modelValue", false);
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape" && !app.confirmation) {
    e.preventDefault();
    close();
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      window.addEventListener("keydown", onKeydown);
    } else {
      window.removeEventListener("keydown", onKeydown);
    }
  },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});

function focusNode(id: string): void {
  store.selectedNode = id;
  store.selectedEdgeId = "";
  vis?.focusNode(id, 1.4);
  close();
}

function focusEdge(edge: GraphEdge): void {
  const edgeId = edge.id || `${edge.from}_${edge.to}`;
  vis?.focusNode(edge.from, 1.28);
  store.selectedNode = "";
  store.selectedEdgeId = edgeId;
  close();
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="dialog-overlay" @click.self="close">
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dict-dialog-title">
        <div class="panel-title">
          <h2 id="dict-dialog-title">图谱内容字典</h2>
          <button class="btn ghost sm" type="button" aria-label="关闭弹窗" @click="close">关闭</button>
        </div>

        <div class="segmented tabular">
          <button
            type="button"
            :class="{ active: tab === 'nodes' }"
            @click="tab = 'nodes'"
          >
            实体 ({{ rawNodes.length }})
          </button>
          <button
            type="button"
            :class="{ active: tab === 'edges' }"
            @click="tab = 'edges'"
          >
            关系 ({{ rawEdges.length }})
          </button>
        </div>

        <div class="field">
          <label for="graph-dict-filter">关键词过滤</label>
          <input
            id="graph-dict-filter"
            v-model="filter"
            class="input"
            placeholder="输入实体名称、ID 或关系谓词过滤（最多显示前 160 项）"
          />
        </div>

        <div class="dict-list">
          <template v-if="tab === 'nodes'">
            <div v-if="filteredNodes.length === 0" class="empty">没有匹配的图谱实体</div>
            <div v-else class="rows">
              <div v-for="(node, index) in filteredNodes" :key="node.id" class="dict-row">
                <div class="dict-main">
                  <strong class="tabular">{{ index + 1 }}. {{ node.label || node.id }}</strong>
                  <div class="tags">
                    <span class="tag mono">{{ node.id }}</span>
                    <span v-if="node.is_ghost" class="tag warn">幽灵节点</span>
                    <span v-if="node.is_deleted" class="tag bad">已删除</span>
                  </div>
                </div>
                <button class="btn sm" type="button" @click="focusNode(node.id)">定位实体</button>
              </div>
            </div>
          </template>

          <template v-else>
            <div v-if="filteredEdges.length === 0" class="empty">没有匹配的图谱关系</div>
            <div v-else class="rows">
              <div
                v-for="(edge, index) in filteredEdges"
                :key="edge.id || `${edge.from}_${edge.to}_${index}`"
                class="dict-row"
              >
                <div class="dict-main">
                  <strong class="tabular">{{ index + 1 }}. {{ edge.from }} → {{ edge.to }}</strong>
                  <div class="tags">
                    <span class="tag">{{ edge.label || "关联" }}</span>
                    <span class="tag mono">权重 {{ formatEdgeWeight(edge.value) }}</span>
                    <span v-if="edge.is_pinned" class="tag warn">置顶</span>
                    <span v-if="edge.is_protected" class="tag ok">保护中</span>
                    <span v-if="edge.is_active === false" class="tag bad">冷冻</span>
                  </div>
                </div>
                <button class="btn sm" type="button" @click="focusEdge(edge)">定位关系</button>
              </div>
            </div>
          </template>
        </div>

        <div class="dialog-actions">
          <button class="btn" type="button" @click="close">关闭</button>
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

.dict-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
  border-top: 1px solid var(--hairline);
}

.dict-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
}

.dict-main {
  min-width: 0;
  flex: 1;
}

.dict-main strong {
  display: block;
  font-size: 13.5px;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
  border-top: 1px solid var(--hairline);
}
</style>
