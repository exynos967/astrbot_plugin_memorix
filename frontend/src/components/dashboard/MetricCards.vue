<script setup lang="ts">
// 顶部三块指标卡：图谱节点 / 关系 / 向量。
// 纯展示组件（props down），数据来自 useDashboardStore.stats。
// 从 legacy dashboard DOM（index.html 行 2053-2066）迁移。
defineProps<{
  /** 图谱节点总量（stats.graph_store.num_nodes）。 */
  nodes: number | null;
  /** 图边/关系总量（stats.graph_store.num_edges）。 */
  edges: number | null;
  /** 向量库条数（stats.vector_store.num_vectors）。 */
  vectors: number | null;
}>();

function display(value: number | null): string {
  return value == null ? "-" : String(value);
}
</script>

<template>
  <div class="grid-3">
    <div class="metric">
      <div>
        <small>Graph nodes</small>
        <strong>{{ display(nodes) }}</strong>
        <p>实体节点总量</p>
      </div>
      <div class="bubble blue">G</div>
    </div>
    <div class="metric">
      <div>
        <small>Relations</small>
        <strong>{{ display(edges) }}</strong>
        <p>图边与关系映射</p>
      </div>
      <div class="bubble teal">R</div>
    </div>
    <div class="metric">
      <div>
        <small>Vectors</small>
        <strong>{{ display(vectors) }}</strong>
        <p>向量库状态</p>
      </div>
      <div class="bubble pink">V</div>
    </div>
  </div>
</template>
