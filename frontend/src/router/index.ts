import { createRouter, createWebHashHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";
import { useCandidateStore } from "@/stores/candidate";
import { useUiStore } from "@/stores/ui";

export interface NavItem {
  name: string;
  path: string;
  icon: string;
  label: string;
  title: string;
  subtitle: string;
  group: string;
  badge?: string;
}

export interface NavGroup {
  id: string;
  label: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: "overview",
    label: "概览与检索",
    items: [
      {
        name: "dashboard",
        path: "/dashboard",
        icon: "dashboard",
        label: "运行总览",
        title: "运行总览",
        subtitle: "存储体量、子系统状态与近期调用趋势",
        group: "overview",
      },
      {
        name: "graph",
        path: "/graph",
        icon: "graph",
        label: "知识图谱",
        title: "知识图谱",
        subtitle: "查看与编辑实体节点、关系边及来源关联",
        group: "overview",
      },
      {
        name: "query",
        path: "/query",
        icon: "query",
        label: "记忆检索",
        title: "记忆检索",
        subtitle: "按语义、时间线、关系与情景组合查询记忆",
        group: "overview",
      },
    ],
  },
  {
    id: "assets",
    label: "记忆资产",
    items: [
      {
        name: "memory",
        path: "/memory",
        icon: "memory",
        label: "记忆与事实",
        title: "记忆与事实",
        subtitle: "管理事实台账、权重保护、向量维护与回收站",
        group: "assets",
      },
      {
        name: "images",
        path: "/images",
        icon: "images",
        label: "图片记忆",
        title: "图片记忆",
        subtitle: "管理入库图片、以图搜图与视觉向量索引",
        group: "assets",
      },
      {
        name: "episodes",
        path: "/episodes",
        icon: "episodes",
        label: "事件摘要",
        title: "事件摘要",
        subtitle: "查看会话情景摘要并按来源重建片段",
        group: "assets",
      },
      {
        name: "people",
        path: "/people",
        icon: "people",
        label: "人物画像",
        title: "人物画像",
        subtitle: "查看会话人物档案、别名与人工修订记录",
        group: "assets",
      },
      {
        name: "sources",
        path: "/sources",
        icon: "sources",
        label: "来源追溯",
        title: "来源追溯",
        subtitle: "核查原始文档与会话段落证据，按来源清理",
        group: "assets",
      },
    ],
  },
  {
    id: "governance",
    label: "迁移与治理",
    items: [
      {
        name: "bundles",
        path: "/bundles",
        icon: "bundles",
        label: "知识包与迁移",
        title: "知识包与会话迁移",
        subtitle: "导入导出 .amembundle 知识包，迁移在线会话记忆",
        group: "governance",
      },
      {
        name: "correction",
        path: "/correction",
        icon: "correction",
        label: "纠错与调优",
        title: "纠错与调优",
        subtitle: "执行自然语言修正、反馈审计与检索参数调优",
        group: "governance",
      },
      {
        name: "import",
        path: "/import",
        icon: "import",
        label: "导入与总结",
        title: "导入与总结",
        subtitle: "导入外部语料、触发会话总结与跟踪后台任务",
        group: "governance",
      },
    ],
  },
  {
    id: "system",
    label: "系统与审计",
    items: [
      {
        name: "settings",
        path: "/settings",
        icon: "settings",
        label: "运行设置",
        title: "运行设置",
        subtitle: "配置模型与存储参数、执行联通性自检与界面偏好",
        group: "system",
      },
      {
        name: "logs",
        path: "/logs",
        icon: "logs",
        label: "操作日志",
        title: "操作日志",
        subtitle: "筛选本页操作记录与异常诊断信息",
        group: "system",
      },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

const REAL_VIEWS: Record<string, () => Promise<unknown>> = {
  dashboard: () => import("@/views/DashboardView.vue"),
  graph: () => import("@/views/GraphView.vue"),
  query: () => import("@/views/QueryView.vue"),
  memory: () => import("@/views/MemoryView.vue"),
  images: () => import("@/views/ImagesView.vue"),
  episodes: () => import("@/views/EpisodesView.vue"),
  people: () => import("@/views/PeopleView.vue"),
  sources: () => import("@/views/SourcesView.vue"),
  bundles: () => import("@/views/BundlesView.vue"),
  correction: () => import("@/views/CorrectionView.vue"),
  import: () => import("@/views/ImportView.vue"),
  settings: () => import("@/views/SettingsView.vue"),
  logs: () => import("@/views/LogsView.vue"),
};

function buildRoutes(): RouteRecordRaw[] {
  return [
    { path: "/", redirect: "/dashboard" },
    ...NAV_ITEMS.map((item) => ({
      path: item.path,
      name: item.name,
      component: REAL_VIEWS[item.name] ?? (() => import("@/components/shell/PlaceholderView.vue")),
      meta: { title: item.title, subtitle: item.subtitle, group: item.group },
    })),
    { path: "/:pathMatch(.*)*", redirect: "/dashboard" },
  ];
}

export const router = createRouter({
  history: createWebHashHistory(),
  routes: buildRoutes(),
});

router.afterEach(() => {
  try {
    useCandidateStore().detach();
    useUiStore().setMobileNav(false);
  } catch {
    // Pinia 初始化前导航忽略
  }
});
