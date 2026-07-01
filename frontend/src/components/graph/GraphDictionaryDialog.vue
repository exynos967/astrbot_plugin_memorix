<template>
  <div v-if="modelValue" class="dialog-overlay" @click.self="close">
    <div class="dialog">
      <h2 class="panel-title">内容字典</h2>

      <!-- tab 切换 -->
      <div class="segmented">
        <button
          class="btn"
          :class="{ primary: tab === 'nodes' }"
          @click="tab = 'nodes'"
        >
          实体 ({{ rawNodes.length }})
        </button>
        <button
          class="btn"
          :class="{ primary: tab === 'edges' }"
          @click="tab = 'edges'"
        >
          关系 ({{ rawEdges.length }})
        </button>
      </div>

      <!-- 搜索过滤 -->
      <div class="field">
        <input v-model="filter" class="input" placeholder="输入关键词过滤" />
      </div>

      <!-- 列表 -->
      <div class="result-list">
        <!-- 实体 tab -->
        <template v-if="tab === 'nodes'">
          <div v-if="filteredNodes.length === 0" class="empty">没有匹配节点</div>
          <div
            v-for="(node, index) in filteredNodes"
            :key="node.id"
            class="result"
          >
            <div class="result-head">
              <h3>{{ index + 1 }}. {{ node.label || node.id }}</h3>
              <button class="btn" @click="focusNode(node.id)">定位</button>
            </div>
            <div class="tags">
              <span class="tag">{{ node.id }}</span>
              <span v-if="node.is_ghost" class="tag warn">幽灵节点</span>
              <span v-if="node.is_deleted" class="tag bad">已删除</span>
            </div>
          </div>
        </template>

        <!-- 关系 tab -->
        <template v-else>
          <div v-if="filteredEdges.length === 0" class="empty">没有匹配关系</div>
          <div
            v-for="(edge, index) in filteredEdges"
            :key="edge.id || `${edge.from}_${edge.to}_${index}`"
            class="result"
          >
            <div class="result-head">
              <h3>{{ index + 1 }}. {{ edge.from }} → {{ edge.to }}</h3>
              <button class="btn" @click="focusEdge(edge.from)">定位</button>
            </div>
            <div class="tags">
              <span class="tag">{{ edge.label || "关系" }}</span>
              <span class="tag">weight {{ Number(edge.value || 1).toFixed(1) }}</span>
            </div>
          </div>
        </template>
      </div>

      <div class="toolbar">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, inject } from "vue";
import { storeToRefs } from "pinia";
import { useGraphStore } from "@/stores/graph";
import { GRAPH_VIS_KEY, type VisController } from "@/composables/useVisNetwork";

defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const store = useGraphStore();
const { rawNodes, rawEdges } = storeToRefs(store);

// vis 控制器可能为 null（GraphView 未 provide 完成时）
const vis = inject<VisController | null>(GRAPH_VIS_KEY, null);

// 本地 tab，默认实体
const tab = ref<"nodes" | "edges">("nodes");
// 过滤关键词
const filter = ref("");

// 实体过滤：label / id 含 filter，不区分大小写，截断 160 项
const filteredNodes = computed(() => {
  const q = filter.value.trim().toLowerCase();
  return rawNodes.value
    .filter((node) =>
      [node.id, node.label].join(" ").toLowerCase().includes(q)
    )
    .slice(0, 160);
});

// 关系过滤：from / to / label 含 filter，截断 160 项
const filteredEdges = computed(() => {
  const q = filter.value.trim().toLowerCase();
  return rawEdges.value
    .filter((edge) =>
      [edge.from, edge.to, edge.label, ...(edge.predicates || [])]
        .join(" ")
        .toLowerCase()
        .includes(q)
    )
    .slice(0, 160);
});

// 定位节点：聚焦并关闭弹窗
function focusNode(id: string) {
  if (!vis) return;
  vis.focusNode(id, 1.4);
  close();
}

// 定位关系：聚焦起点节点
function focusEdge(from: string) {
  if (!vis) return;
  vis.focusNode(from);
  close();
}

function close() {
  emit("update:modelValue", false);
}
</script>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.dialog {
  min-width: 420px;
  max-width: 90vw;
  max-height: 80vh;
  overflow: auto;
  background: var(--bg, #fff);
  border-radius: 10px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
