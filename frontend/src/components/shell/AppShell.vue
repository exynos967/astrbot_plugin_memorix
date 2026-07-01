<script setup lang="ts">
// 应用外壳：侧栏 + 顶栏 + 内容区（<RouterView />）。
// 从 legacy .app/.shell/.workspace 结构（index.html 行 2018-2051）迁移。
import Sidebar from "./Sidebar.vue";
import TopBar from "./TopBar.vue";
</script>

<template>
  <div class="app">
    <div class="app-title">
      <strong>A_MEMORIX CONTROL PANEL</strong>
      <span>AstrBot embedded dashboard</span>
    </div>
    <div class="shell">
      <Sidebar />
      <main class="workspace">
        <TopBar />
        <div class="view-stack">
          <RouterView v-slot="{ Component }">
            <component :is="Component" />
          </RouterView>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.app-title {
  padding: 10px 24px;
  display: flex;
  align-items: baseline;
  gap: 12px;
  border-bottom: 1px solid var(--hairline);
  background: var(--shell);
  backdrop-filter: var(--blur-soft);
}

.app-title strong {
  font-size: 13px;
  letter-spacing: 1px;
  color: var(--accent-ink);
}

.app-title span {
  font-size: 11px;
  color: var(--muted-2);
}

.shell {
  flex: 1;
  display: flex;
  min-height: 0;
}

.workspace {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.view-stack {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: auto;
  padding: 24px 28px;
}

.view-stack--fixed {
  overflow: hidden;
}

/* 不设 :deep(> *) flex:1 / min-height:0——flex 列子项 min-height:0 会压制
 * 默认 min-height:auto（内容最小高度），子项 flex-shrink:1 配合 → 子项被压缩到
 * 容器高度，view-stack overflow:auto 永远看不到溢出 → 滚动条不出、内容截断。
 * 各 view 自己管理高度：GraphView（.view-graph flex:1 1 0 + min-height:0）
 * 需要撑满画布，其他 view 内容自然撑高，由 view-stack 滚动。 */
</style>
