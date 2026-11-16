<script setup lang="ts">
// 服务分区列表：核心子系统实时状态与快捷跳转（使用线性矢量图标，零单字气泡、零紫红/品红色调）。
import { computed } from "vue";
import NavIcons from "@/components/shell/NavIcons.vue";
import { useDashboardStore } from "@/stores/dashboard";
import {
  deriveVectorSummary,
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
  iconName: string;
  scope: string;
  metric: string;
  note: string;
  status: string;
  view: string;
  toneClass: "blue" | "teal" | "amber" | "green";
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
    episode: { status: "ready", count: 0, queue: { counts: { pending: 0, running: 0, failed: 0 } } },
    import: { status: "ready", latest_task: null },
    person: { status: "ready", profile_count: 0 },
    runtime: { status: "unknown", report: null },
  };
  const stats = dashboard.stats;
  const metadataStats = stats?.metadata_store ?? {};
  const graphStats = stats?.graph_store ?? { num_nodes: 0, num_edges: 0 };
  const vecSummary = deriveVectorSummary(
    services.graph.vectors ?? stats?.vector_store.num_vectors,
    stats?.vector_store.dimension,
    dashboard.runtime ?? services.runtime.report,
  );
  const latestImport = services.import.latest_task ?? null;
  const epCounts = (
    services.episode.queue as {
      counts?: { pending?: number; running?: number; failed?: number };
    } | null
  )?.counts;
  const epPending = epCounts?.pending ?? 0;
  const epRunning = epCounts?.running ?? 0;
  const epFailed = epCounts?.failed ?? 0;

  const runtimeFallback = dashboard.runtime
    ? dashboard.runtime.ok
      ? "ready"
      : dashboard.runtime.code === "runtime_components_missing"
        ? "degraded"
        : "failed"
    : "unknown";

  const fb = dashboard.feedbackSummary;

  return [
    {
      title: "知识图谱与双池索引",
      code: "图谱引擎",
      iconName: "graph",
      scope: "实体节点、关系边与双池向量",
      metric: `${services.graph.nodes ?? graphStats.num_nodes ?? 0} 实体 / ${services.graph.relations ?? graphStats.num_edges ?? 0} 关系 / ${vecSummary.total ?? 0} 向量`,
      note: vecSummary.isDual
        ? `双池：段落 ${vecSummary.paragraphVectors} · 图谱 ${vecSummary.graphVectors}`
        : "实时存储统计",
      status: services.graph.status || "ready",
      view: "graph",
      toneClass: "blue",
    },
    {
      title: "统一检索中心",
      code: "7 路检索",
      iconName: "query",
      scope: "智能路由 / 多路聚合 / 语义 / 时序 / 情景",
      metric: `近 1 分钟 ${services.query.recent_count ?? 0} 次调用`,
      note:
        services.query.recent_total_count != null
          ? `累计记录 ${services.query.recent_total_count} 次检索`
          : "检索调度就绪",
      status: statusWithBusy(dashboard.busy, "query", services.query.status || "ready"),
      view: "query",
      toneClass: "teal",
    },
    {
      title: "情景记忆队列",
      code: "租约队列",
      iconName: "episodes",
      scope: "事件聚合、缓存复用与在线重建",
      metric: `${services.episode.count ?? metadataStats.episode_count ?? 0} 条情景`,
      note:
        epFailed > 0
          ? `待处理 ${epPending} / 运行 ${epRunning} / 失败 ${epFailed}`
          : `租约队列：${epPending} 待处理 / ${epRunning} 运行中`,
      status: statusWithBusy(
        dashboard.busy,
        "episode",
        epFailed > 0 ? "degraded" : services.episode.status || "ready",
      ),
      view: "episodes",
      toneClass: "teal",
    },
    {
      title: "记忆导入与摘要任务",
      code: "异步管线",
      iconName: "import",
      scope: "文本 / 关系 / 对话日志 / 文件流式导入",
      metric: latestImport
        ? `最近任务：${statusLabel(String(latestImport.status))}`
        : "当前无导入任务",
      note: latestImport?.task_id
        ? `任务 ID ${String(latestImport.task_id).slice(0, 8)}`
        : "异步导入队列空闲",
      status: statusWithBusy(dashboard.busy, "import", services.import.status || "ready"),
      view: "import",
      toneClass: "amber",
    },
    {
      title: "人物画像与事实台账",
      code: "Schema v31",
      iconName: "people",
      scope: "人物卡片、别名治理与结构化事实",
      metric: `${services.person.profile_count ?? metadataStats.person_profile_count ?? 0} 份人物画像`,
      note: "6 大标准画像分区与人工修订",
      status: statusWithBusy(dashboard.busy, "person", services.person.status || "ready"),
      view: "people",
      toneClass: "green",
    },
    {
      title: "纠错与参数调优",
      code: "记忆修正",
      iconName: "correction",
      scope: "自然语言纠错预览、应用与回滚",
      metric: fb
        ? fb.enabled
          ? `累计 ${fb.count} 条纠错记录`
          : "反馈服务按需启用"
        : "在线纠错就绪",
      note: "联动事实台账与可恢复删除审计",
      status: fb?.status || "ready",
      view: "correction",
      toneClass: "blue",
    },
    {
      title: "运行时与系统设置",
      code: "自检配置",
      iconName: "settings",
      scope: "Embedding 连通性、衰减策略与模型参数",
      metric: runtimeMessageText(
        dashboard.runtime ?? services.runtime.report,
        "等待运行时自检",
      ),
      note: "系统参数配置与运行时自检",
      status: services.runtime.status || runtimeFallback,
      view: "settings",
      toneClass: "teal",
    },
  ];
});
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>服务分区状态</h2>
      <span class="section-label">各子系统实时状态与快捷入口</span>
    </div>
    <div class="table-list">
      <div v-for="row in rows" :key="row.view" class="row-item">
        <div class="row-main">
          <span class="icon-badge" :class="row.toneClass" aria-hidden="true">
            <NavIcons :name="row.iconName" :size="16" />
          </span>
          <div class="row-main-copy">
            <strong>{{ row.title }}</strong>
            <span>{{ row.code }}</span>
          </div>
        </div>
        <div class="service-metric">
          <strong>{{ row.scope }}</strong>
          <span class="cell-note">功能范围</span>
        </div>
        <div class="service-metric">
          <strong>{{ row.metric }}</strong>
          <span class="cell-note">{{ row.note }}</span>
        </div>
        <div>
          <span class="status-pill" :class="statusTone(row.status)">
            {{ statusLabel(row.status) }}
          </span>
          <span class="cell-note">运行状态</span>
        </div>
        <button
          class="btn icon"
          type="button"
          :title="`前往${row.title}`"
          :aria-label="`前往${row.title}`"
          @click="emit('navigate', row.view)"
        >
          <NavIcons name="chevron-right" :size="16" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row-main-copy {
  min-width: 0;
}
</style>
