import { createRouter, createWebHashHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";

// 导航项配置：router 与 Sidebar 共用，避免双写（DRY）。
// title/subtitle 从 legacy titles 字典（index.html 行 2538-2549）原样迁移。
export interface NavItem {
  name: string;
  path: string;
  icon: string;
  label: string;
  title: string;
  subtitle: string;
}

export const NAV_ITEMS: NavItem[] = [
  { name: "dashboard", path: "/dashboard", icon: "D", label: "总览", title: "Dashboard", subtitle: "运行状态、图谱体量与核心维护入口" },
  { name: "graph", path: "/graph", icon: "G", label: "图谱", title: "Knowledge Graph", subtitle: "浏览实体、关系、来源和记忆状态" },
  { name: "query", path: "/query", icon: "Q", label: "查询", title: "Query Center", subtitle: "统一语义、时间、关系与 episode 查询" },
  { name: "episodes", path: "/episodes", icon: "E", label: "情景", title: "Episodes", subtitle: "情景记忆检索、详情与按 source 重建" },
  { name: "import", path: "/import", icon: "I", label: "导入", title: "Import Center", subtitle: "导入任务、摘要任务与任务状态" },
  { name: "people", path: "/people", icon: "P", label: "人物", title: "Person Profiles", subtitle: "人物画像、候选列表与人工覆盖" },
  { name: "sources", path: "/sources", icon: "S", label: "来源", title: "Sources", subtitle: "来源文件、段落证据与批量清理" },
  { name: "memory", path: "/memory", icon: "M", label: "记忆", title: "Memory Ops", subtitle: "强化、保护、冷冻与回收站恢复" },
  { name: "settings", path: "/settings", icon: "C", label: "设置", title: "Settings", subtitle: "配置、自检、保存和访问令牌" },
  { name: "logs", path: "/logs", icon: "L", label: "日志", title: "Activity Logs", subtitle: "近期 API 请求、自检与配置变更记录" },
];

// 已实现 view 映射；全部走动态 import 拆 chunk，首屏仅加载当前 view（graph 含 vis-network 单独 chunk）。
const REAL_VIEWS: Record<string, () => Promise<unknown>> = {
  dashboard: () => import("@/views/DashboardView.vue"),
  settings: () => import("@/views/SettingsView.vue"),
  memory: () => import("@/views/MemoryView.vue"),
  people: () => import("@/views/PeopleView.vue"),
  sources: () => import("@/views/SourcesView.vue"),
  episodes: () => import("@/views/EpisodesView.vue"),
  import: () => import("@/views/ImportView.vue"),
  query: () => import("@/views/QueryView.vue"),
  graph: () => import("@/views/GraphView.vue"),
  logs: () => import("@/views/LogsView.vue"),
};

function buildRoutes(): RouteRecordRaw[] {
  const routes: RouteRecordRaw[] = [
    { path: "/", redirect: "/dashboard" },
    ...NAV_ITEMS.map((item) => ({
      path: item.path,
      name: item.name,
      component: REAL_VIEWS[item.name] ?? (() => import("@/components/shell/PlaceholderView.vue")),
      meta: { title: item.title, subtitle: item.subtitle },
    })),
  ];
  return routes;
}

export const router = createRouter({
  history: createWebHashHistory(),
  routes: buildRoutes(),
});
