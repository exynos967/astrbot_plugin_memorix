<script setup lang="ts">
// Settings 组合面：界面偏好 / 运行配置 / 自检 / 高级配置 / 保存与安全。
// 从 legacy view-settings（index.html 行 2354-2421）迁移为子组件组合。
// 数据：配置/自检复用 useDashboardStore（dashboard 在 onMounted 加载），
// 表单状态在 useSettingsStore。挂载时确保 config 已加载并初始化表单。
import { computed, onMounted } from "vue";
import UiPrefPanel from "@/components/settings/UiPrefPanel.vue";
import ConfigForm from "@/components/settings/ConfigForm.vue";
import SelfCheckPanel from "@/components/settings/SelfCheckPanel.vue";
import { useDashboardStore } from "@/stores/dashboard";
import { useSettingsStore } from "@/stores/settings";
import { useLogsStore } from "@/stores/logs";

const dashboard = useDashboardStore();
const settings = useSettingsStore();
const logs = useLogsStore();

const rawConfigJson = computed(() => JSON.stringify(settings.rawConfig ?? {}, null, 2));

/** 保存到运行时 + 记录活动日志。 */
async function onSaveRuntime(): Promise<void> {
  const ok = await settings.saveRuntime();
  if (ok) logs.log("运行配置已保存到运行时", "info");
}

async function onPersist(): Promise<void> {
  await settings.persistConfig();
}

async function onRefreshConfig(): Promise<void> {
  await dashboard.loadConfig();
  if (dashboard.config) settings.initFromConfig(dashboard.config);
}

onMounted(async () => {
  if (!dashboard.config) {
    await dashboard.loadConfig();
  }
  if (dashboard.config) settings.initFromConfig(dashboard.config);
});
</script>

<template>
  <section class="view-settings">
    <UiPrefPanel />

    <div class="band">
      <div class="panel-title">
        <h2>运行配置</h2>
        <div class="toolbar">
          <button class="btn" @click="onRefreshConfig">刷新配置</button>
          <button class="btn primary" :disabled="settings.saving" @click="onSaveRuntime">保存到运行时</button>
          <button class="btn" :disabled="settings.saving" @click="onPersist">写回配置文件</button>
        </div>
      </div>
      <ConfigForm />
    </div>

    <SelfCheckPanel @force="settings.refreshSelfCheck()" />

    <div class="band">
      <div class="panel-title">
        <h2>高级配置</h2>
        <span class="section-label">脱敏只读</span>
      </div>
      <details class="advanced-panel">
        <summary>查看完整配置 JSON</summary>
        <pre class="json" style="margin-top: 10px">{{ rawConfigJson }}</pre>
      </details>
    </div>

    <div class="band">
      <div class="panel-title"><h2>保存与安全</h2></div>
      <div class="summary-grid token-status-grid">
        <div class="summary-item"><span>当前入口</span><strong>Dashboard 已鉴权</strong></div>
        <div class="summary-item"><span>接口保护</span><strong>AstrBot Dashboard</strong></div>
        <div class="summary-item"><span>配置文件写回</span><strong>请在 AstrBot 插件配置页持久化配置</strong></div>
      </div>
      <div class="toolbar" style="margin-top: 12px">
        <button class="btn primary" @click="settings.manualSaveAll()">手动保存</button>
        <button class="btn" @click="settings.toggleAutoSave(true)">开启自动保存</button>
        <button class="btn" @click="settings.toggleAutoSave(false)">关闭自动保存</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.view-settings {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.token-status-grid {
  margin-bottom: 12px;
}
</style>
