<script setup lang="ts">
// 服务分区列表：6 行核心功能入口卡片。
// 从 legacy renderServiceRows（index.html 行 2847-2940）迁移为 computed 派生。
// 数据来自 useDashboardStore（status / stats / busy / runtime）。
// 导航按钮上抛 navigate(view) → DashboardView 调 router.push（消除 legacy onclick=setView）。
import { computed } from "vue";
import { useDashboardStore } from "@/stores/dashboard";
import {
  runtimeMessageText,
  statusLabel,
  statusTone,
  statusWithBusy,
} from "@/utils/dashboardText";

const emit = defineEmits<{ navigate: [view: string] }>();

const dashboard = useDashboardStore();

interface ServiceRow {
  title: string;
  code: string;
  icon: string;
  scope: string;
  metric: string;
  note: string;
  status: string;
  view: string;
  color: string;
}

const rows = computed<ServiceRow[]>(() => {
  const services = dashboard.status?.services ?? {
    graph: { status: "ready", nodes: 0, relations: 0, vectors: 0 },
    query: {
      status: "ready",
      recent_seconds: 60,
      recent_count: 0,
      recent_total_count: 0,
      trend_seconds: 7200,
      trend_bucket_seconds: 300,
      trend_total_count: 0,
      trend_buckets: [],
    },
    episode: { status: "ready", count: 0, queue: { counts: { pending: 0, running: 0 } } },
    import: { status: "ready", latest_task: null },
    person: { status: "ready", profile_count: 0 },
    runtime: { status: "unknown", report: null },
  };
  const stats = dashboard.stats;
  const metadataStats = stats?.metadata_store ?? {};
  const vectorStats = stats?.vector_store ?? { num_vectors: 0 };
  const graphStats = stats?.graph_store ?? { num_nodes: 0, num_edges: 0 };
  const latestImport = services.import.latest_task ?? null;
  const runtimeChip =
    dashboard.runtime
      ? (dashboard.runtime.ok ? "ok" : dashboard.runtime.code === "runtime_components_missing" ? "missing" : "error")
      : "unknown";

  return [
    {
      title: "图谱浏览",
      code: "Graph",
      icon: "G",
      scope: "节点、关系与向量索引",
      metric: `${services.graph.nodes ?? graphStats.num_nodes ?? 0} 节点 / ${services.graph.relations ?? graphStats.num_edges ?? 0} 关系 / ${services.graph.vectors ?? vectorStats.num_vectors ?? 0} 向量`,
      note: "实时存储体量",
      status: services.graph.status || "ready",
      view: "graph",
      color: "#2388ff",
    },
    {
      title: "聚合查询",
      code: "Query",
      icon: "Q",
      scope: "search / time / episode",
      metric: `近 1 分钟 ${services.query.recent_count ?? 0} 次`,
      note:
        services.query.recent_total_count != null
          ? `查询中心合计 ${services.query.recent_total_count} 次`
          : "聚合查询调用次数",
      status: statusWithBusy(dashboard.busy, "query", services.query.status || "ready"),
      view: "query",
      color: "#111111",
    },
    {
      title: "Episode",
      code: "Episode",
      icon: "E",
      scope: "情景生成与检索",
      metric: `${services.episode.count ?? metadataStats.episode_count ?? 0} 条情景`,
      note: `队列 ${(services.episode.queue as { counts?: { pending?: number } })?.counts?.pending ?? 0} 等待 / ${(services.episode.queue as { counts?: { running?: number } })?.counts?.running ?? 0} 运行`,
      status: statusWithBusy(dashboard.busy, "episode", services.episode.status || "ready"),
      view: "episodes",
      color: "#e9ece7",
    },
    {
      title: "导入任务",
      code: "Import",
      icon: "I",
      scope: "text / relation / json / file",
      metric: latestImport ? `最近任务 ${statusLabel(String(latestImport.status))}` : "暂无任务",
      note: latestImport?.task_id ? `ID ${String(latestImport.task_id).slice(0, 8)}` : "导入队列空闲",
      status: statusWithBusy(dashboard.busy, "import", services.import.status || "ready"),
      view: "import",
      color: "#b7dfff",
    },
    {
      title: "人物画像",
      code: "Profile",
      icon: "P",
      scope: "自动画像与人工覆盖",
      metric: `${services.person.profile_count ?? metadataStats.person_profile_count ?? 0} 个画像`,
      note: "按 person_id 计数",
      status: statusWithBusy(dashboard.busy, "person", services.person.status || "ready"),
      view: "people",
      color: "#f1f1ee",
    },
    {
      title: "Runtime",
      code: "Check",
      icon: "C",
      scope: "embedding 维度自检",
      metric: runtimeMessageText(services.runtime.report, runtimeChip === "unknown" ? "等待自检" : runtimeChip),
      note: "运行时健康状态",
      status: services.runtime.status || runtimeChip || "unknown",
      view: "settings",
      color: "#d7dad3",
    },
  ];
});

function iconColor(idx: number): string {
  return idx === 0 || idx === 1 ? "#ffffff" : "#111111";
}

function nodeMarkStyle(row: ServiceRow, idx: number): Record<string, string> {
  return { background: row.color, color: iconColor(idx) };
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>服务分区</h2>
      <span class="section-label">核心功能入口</span>
    </div>
    <div class="table-list">
      <div v-for="(row, idx) in rows" :key="row.view" class="row-item">
        <div class="row-main">
          <div class="node-mark" :style="nodeMarkStyle(row, idx)">{{ row.icon }}</div>
          <div><strong>{{ row.title }}</strong><span>{{ row.code }}</span></div>
        </div>
        <div class="service-metric"><strong>{{ row.scope }}</strong><span class="cell-note">功能范围</span></div>
        <div class="service-metric"><strong>{{ row.metric }}</strong><span class="cell-note">{{ row.note }}</span></div>
        <div>
          <span class="status-pill" :class="statusTone(row.status)">{{ statusLabel(row.status) }}</span>
          <span class="cell-note">当前状态</span>
        </div>
        <button class="btn icon" title="进入" @click="emit('navigate', row.view)">›</button>
      </div>
    </div>
  </div>
</template>
