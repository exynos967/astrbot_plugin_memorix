<script setup lang="ts">
// 应用外壳：无障碍跳链 + 侧栏 + 顶栏 + 主工作区（含路由视图单次入场动效）。
// 在外壳挂载时统一执行 loadScopes()，保证直达任意路由时均立刻持有 resolvedScope 与可选作用域列表。
import { onMounted } from "vue";
import { useScope } from "@/composables/useScope";
import Sidebar from "./Sidebar.vue";
import TopBar from "./TopBar.vue";

const { loadScopes } = useScope();

onMounted(() => {
  void loadScopes();
});
</script>

<template>
  <div class="app">
    <a class="skip-link" href="#workspace-main">跳至主内容</a>
    <div class="shell">
      <Sidebar />
      <main class="workspace">
        <TopBar />
        <div id="workspace-main" class="view-stack" tabindex="-1">
          <RouterView v-slot="{ Component, route }">
            <component :is="Component" :key="route.path" class="view-enter" />
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
  overflow-x: hidden;
  overflow-y: auto;
  padding: 20px 24px 28px;
}

.view-stack:focus {
  outline: none;
}

.view-stack--fixed {
  overflow: hidden;
}

/* 路由挂载单次入场（220ms，完成后不残留 transform 避免破坏后代 fixed 定位） */
:deep(.view-enter) {
  animation: viewEnter 220ms var(--ease-enter);
}

@keyframes viewEnter {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  :deep(.view-enter) {
    animation: none;
  }
}

@media (max-width: 860px) {
  .view-stack {
    padding: 16px 16px 24px;
  }
}

@media (max-width: 480px) {
  .view-stack {
    padding: 12px 12px 20px;
  }
}
</style>
