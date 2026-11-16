<script setup lang="ts">
// 统一 18px 线性矢量图标库：覆盖主导航与各业务视图统计卡/工具栏语义符号，消除汉字塞方块与杂乱字符。
defineProps<{
  name: string;
  size?: number;
}>();
</script>

<template>
  <svg
    class="nav-svg"
    :width="size ?? 18"
    :height="size ?? 18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <!-- 运行总览 -->
    <template v-if="name === 'dashboard'">
      <rect x="3" y="3" width="7" height="9" rx="1.6" />
      <rect x="14" y="3" width="7" height="5" rx="1.6" />
      <rect x="14" y="12" width="7" height="9" rx="1.6" />
      <rect x="3" y="16" width="7" height="5" rx="1.6" />
    </template>

    <!-- 知识图谱 / 关系 -->
    <template v-else-if="name === 'graph' || name === 'relation'">
      <circle cx="6" cy="6" r="2.6" />
      <circle cx="18" cy="7" r="2.6" />
      <circle cx="12" cy="18" r="2.8" />
      <path d="M8.5 7L15.4 7M7.5 8.2L10.7 15.6M16.6 9.2L13.3 15.6" />
    </template>

    <!-- 实体节点 -->
    <template v-else-if="name === 'entity' || name === 'node'">
      <polygon points="12 3 20 7.5 20 16.5 12 21 4 16.5 4 7.5" />
      <circle cx="12" cy="12" r="2.4" />
    </template>

    <!-- 向量索引 -->
    <template v-else-if="name === 'vector'">
      <path d="M4 19V5M4 19h14M4 19l11-11" />
      <path d="M12 8h3v3" />
      <circle cx="18" cy="6" r="2" />
    </template>

    <!-- 记忆检索 / 搜索 -->
    <template v-else-if="name === 'query' || name === 'search'">
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.8-3.8" />
      <path v-if="name === 'query'" d="M8.5 11h5M11 8.5v5" />
    </template>

    <!-- 记忆与事实 -->
    <template v-else-if="name === 'memory'">
      <path d="M12 3a6 6 0 0 0-6 6c0 2.2 1.2 4.1 3 5.2V17a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-2.8c1.8-1.1 3-3 3-5.2a6 6 0 0 0-6-6Z" />
      <path d="M9.5 21h5" />
    </template>

    <!-- 事实台账 -->
    <template v-else-if="name === 'fact'">
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 8h8M8 12h8M8 16h5" />
    </template>

    <!-- 图片记忆 -->
    <template v-else-if="name === 'images' || name === 'image'">
      <rect x="3" y="4" width="18" height="16" rx="2.2" />
      <circle cx="8.5" cy="9.5" r="1.6" />
      <path d="m21 15-4.6-4.6a1.5 1.5 0 0 0-2.1 0L7 17.7" />
    </template>

    <!-- 事件摘要 -->
    <template v-else-if="name === 'episodes'">
      <path d="M4 6h16M4 12h16M4 18h10" />
      <circle cx="18" cy="18" r="2.5" />
    </template>

    <!-- 人物画像 -->
    <template v-else-if="name === 'people'">
      <circle cx="9" cy="8" r="3.5" />
      <path d="M3.5 19.5a5.8 5.8 0 0 1 11 0" />
      <path d="M16 6.8a3 3 0 0 1 0 5.4M17.5 14.2a4.8 4.8 0 0 1 3 4.8" />
    </template>

    <!-- 来源追溯 -->
    <template v-else-if="name === 'sources'">
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z" />
      <path d="M14 3v6h6M8 13h8M8 17h5" />
    </template>

    <!-- 知识包迁移 -->
    <template v-else-if="name === 'bundles'">
      <path d="M12 2.8 4 7.2v9.6l8 4.4 8-4.4V7.2L12 2.8Z" />
      <path d="M4 7.2 12 11.6l8-4.4M12 11.6v9.6" />
    </template>

    <!-- 在线会话迁移 -->
    <template v-else-if="name === 'chat-migration'">
      <path d="M4 6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H8l-4 3V6Z" />
      <path d="M15 11h3a2 2 0 0 1 2 2v7l-3-2h-5a2 2 0 0 1-2-2v-1" />
      <path d="m11 8 2 1.5L11 11" />
    </template>

    <!-- 纠错与调优 -->
    <template v-else-if="name === 'correction'">
      <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" />
      <path d="M2 14h4M10 8h4M18 16h4" />
    </template>

    <!-- 导入与总结 / 下载 -->
    <template v-else-if="name === 'import' || name === 'download'">
      <path d="M12 3v12m0 0-4-4m4 4 4-4" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </template>

    <!-- 上传 / 导出 -->
    <template v-else-if="name === 'upload'">
      <path d="M12 15V3m0 0-4 4m4-4 4 4" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </template>

    <!-- 运行设置 -->
    <template v-else-if="name === 'settings'">
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
    </template>

    <!-- 操作日志 -->
    <template v-else-if="name === 'logs'">
      <rect x="3" y="4" width="18" height="16" rx="2.2" />
      <path d="m8 10 3 2.5L8 15M13 15h4" />
    </template>

    <!-- 校验勾选 -->
    <template v-else-if="name === 'check'">
      <path d="M20 6 9 17l-5-5" />
    </template>

    <!-- 刷新 -->
    <template v-else-if="name === 'refresh'">
      <path d="M21 12a9 9 0 1 1-2.64-6.36L21 8" />
      <path d="M21 3v5h-5" />
    </template>

    <!-- 关联链接 -->
    <template v-else-if="name === 'link'">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </template>

    <!-- 方向箭头 -->
    <template v-else-if="name === 'chevron-left'">
      <path d="m15 18-6-6 6-6" />
    </template>
    <template v-else-if="name === 'chevron-right'">
      <path d="m9 18 6-6-6-6" />
    </template>

    <!-- 品牌徽标 -->
    <template v-else-if="name === 'brand'">
      <path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z" />
      <circle cx="12" cy="12" r="2.5" />
    </template>

    <!-- 默认兜底 -->
    <template v-else>
      <circle cx="12" cy="12" r="8" />
    </template>
  </svg>
</template>

<style scoped>
.nav-svg {
  display: block;
  flex-shrink: 0;
}
</style>
