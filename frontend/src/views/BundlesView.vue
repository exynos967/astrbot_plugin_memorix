<script setup lang="ts">
// 知识包与会话记忆迁移工作台（/bundles）：
// 子页签 1：离线便携知识包（.amembundle）导出、上传预检与卸载管理
// 子页签 2：在线会话记忆迁移（Schema 31 ChatMemoryMigration）预检、执行与一键恢复
import { onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import BundleExportPanel from "@/components/bundles/BundleExportPanel.vue";
import BundleImportPanel from "@/components/bundles/BundleImportPanel.vue";
import ChatMigrationPanel from "@/components/bundles/ChatMigrationPanel.vue";
import InstalledBundlesList from "@/components/bundles/InstalledBundlesList.vue";
import { useBundlesStore } from "@/stores/bundles";

const route = useRoute();
const store = useBundlesStore();
const activeMode = ref<"bundle" | "chat_migration">("bundle");

watch(
  () => route.query.mode,
  (mode) => {
    const val = Array.isArray(mode) ? mode[0] : mode;
    if (val === "chat_migration" || val === "migration") {
      activeMode.value = "chat_migration";
    } else if (val === "bundle") {
      activeMode.value = "bundle";
    }
  },
  { immediate: true },
);

onMounted(() => {
  void store.loadInstalled();
});
</script>

<template>
  <section class="bundles-view">
    <div class="bundles-header">
      <div class="subtabs" role="tablist" aria-label="知识包与会话迁移模式">
        <button
          type="button"
          role="tab"
          class="subtab-btn"
          :class="{ active: activeMode === 'bundle' }"
          :aria-selected="activeMode === 'bundle'"
          @click="activeMode = 'bundle'"
        >
          离线知识包（.amembundle）
        </button>
        <button
          type="button"
          role="tab"
          class="subtab-btn"
          :class="{ active: activeMode === 'chat_migration' }"
          :aria-selected="activeMode === 'chat_migration'"
          @click="activeMode = 'chat_migration'"
        >
          在线会话迁移
        </button>
      </div>
    </div>

    <div v-if="activeMode === 'bundle'" class="bundles-workbench">
      <div class="bundles-col">
        <BundleExportPanel />
      </div>
      <div class="bundles-col">
        <BundleImportPanel />
        <InstalledBundlesList />
      </div>
    </div>

    <ChatMigrationPanel v-else />
  </section>
</template>

<style scoped>
.bundles-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.bundles-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

.bundles-workbench {
  display: grid;
  grid-template-columns: minmax(0, 1.04fr) minmax(0, 0.96fr);
  gap: 16px;
  align-items: start;
}

.bundles-col {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.bundles-col :deep(.band) {
  margin-top: 0;
}

@media (max-width: 980px) {
  .bundles-workbench {
    grid-template-columns: 1fr;
  }
}
</style>
