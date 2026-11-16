<script setup lang="ts">
// Settings 组合面：
// 1. 界面偏好（UiPrefPanel）
// 2. 运行配置表单（支持 ?group=... 深链定位、脏字段计数、放弃修改与应用生效）
// 3. 运行时与双向量池自检（SelfCheckPanel）
// 4. 存储落盘与自动保存
// 5. 脱敏配置快照
import { computed, onMounted } from "vue";
import UiPrefPanel from "@/components/settings/UiPrefPanel.vue";
import ConfigForm from "@/components/settings/ConfigForm.vue";
import SelfCheckPanel from "@/components/settings/SelfCheckPanel.vue";
import { useDashboardStore } from "@/stores/dashboard";
import { useSettingsStore } from "@/stores/settings";
import { useGraphStore } from "@/stores/graph";

const dashboard = useDashboardStore();
const settings = useSettingsStore();
const graph = useGraphStore();

const rawConfigJson = computed(() => JSON.stringify(settings.rawConfig ?? {}, null, 2));

const scopeLabel = computed(() => graph.effectiveScope() || "默认作用域");

const autoSaveIntervalText = computed(() => {
  const sec = settings.autoSaveInterval;
  if (!sec || sec <= 0) return "-";
  if (sec >= 60) return `每 ${(sec / 60).toFixed(sec % 60 === 0 ? 0 : 1)} 分钟`;
  return `每 ${sec} 秒`;
});

async function onSaveRuntime(): Promise<void> {
  await settings.saveRuntime();
}

async function onPersist(): Promise<void> {
  await settings.persistConfig();
}

async function onRefreshConfig(): Promise<void> {
  await settings.reloadForActiveScope();
}

onMounted(async () => {
  if (!dashboard.config) {
    await settings.reloadForActiveScope();
  } else {
    settings.initFromConfig(dashboard.config);
  }
});
</script>

<template>
  <section class="view-settings">
    <UiPrefPanel />

    <!-- 运行配置主面板 -->
    <div id="settings-config-band" class="band">
      <div class="panel-title">
        <div>
          <h2>运行配置</h2>
          <span class="section-label">当前目标作用域：<code class="mono">{{ scopeLabel }}</code></span>
        </div>
        <div class="toolbar">
          <button
            class="btn"
            type="button"
            :disabled="settings.saving"
            @click="onRefreshConfig"
          >
            重新加载配置
          </button>
          <button
            class="btn"
            type="button"
            :disabled="settings.saving"
            title="尝试写入配置文件；若由 AstrBot 宿主托管配置，将应用至运行时并显示保存指引"
            @click="onPersist"
          >
            写回配置文件
          </button>
          <button
            class="btn primary"
            type="button"
            :disabled="settings.saving || settings.dirtyCount === 0"
            @click="onSaveRuntime"
          >
            {{ settings.saving ? "应用中…" : "应用运行配置" }}
          </button>
        </div>
      </div>

      <!-- 脏状态提示条（不重复主按钮，仅提供快速撤销） -->
      <div v-if="settings.dirtyCount > 0" class="dirty-bar" role="status">
        <div class="dirty-bar-info">
          <span class="tag warn">待应用</span>
          <strong class="tabular-nums">共 {{ settings.dirtyCount }} 项未应用修改</strong>
          <span class="section-label">点击右上角「应用运行配置」立即在当前作用域生效</span>
        </div>
        <button
          class="btn sm"
          type="button"
          :disabled="settings.saving"
          @click="settings.discardChanges()"
        >
          放弃修改
        </button>
      </div>

      <!-- 温和的配置文件持久化说明条 -->
      <div v-if="settings.persistNotice" class="persist-info-bar" role="status">
        <span>{{ settings.persistNotice }}</span>
        <button
          class="btn sm"
          type="button"
          @click="settings.persistNotice = ''"
        >
          关闭提示
        </button>
      </div>

      <ConfigForm />
    </div>

    <!-- 运行时与双向量池自检 -->
    <SelfCheckPanel @force="settings.refreshSelfCheck()" />

    <!-- 存储落盘与自动保存 -->
    <div class="band">
      <div class="panel-title">
        <div>
          <h2>存储落盘与自动保存</h2>
          <span class="section-label">手动刷写双池向量索引、知识图谱与 SQLite 元数据</span>
        </div>
        <div class="tags" style="margin-top: 0">
          <span
            class="tag"
            :class="settings.autoSaveEnabled ? 'ok' : 'warn'"
          >
            {{ settings.autoSaveEnabled ? "自动保存已开启" : "自动保存已关闭" }}
          </span>
          <span class="tag mono tabular-nums">周期：{{ autoSaveIntervalText }}</span>
        </div>
      </div>

      <div class="storage-status-grid">
        <div class="summary-item">
          <span>落盘范围</span>
          <strong>双池向量索引（段落池 + 图谱池）、知识图谱与元数据</strong>
        </div>
        <div class="summary-item">
          <span>鉴权通道</span>
          <strong>AstrBot 宿主安全桥接</strong>
        </div>
        <div class="summary-item">
          <span>跨重启配置</span>
          <strong>由 AstrBot「插件管理 → 配置」统一持久化</strong>
        </div>
      </div>

      <div class="toolbar" style="margin-top: 12px">
        <button
          class="btn primary"
          type="button"
          :disabled="settings.savingStorage"
          @click="settings.manualSaveAll()"
        >
          {{ settings.savingStorage ? "正在落盘…" : "立即落盘索引与图谱" }}
        </button>
        <button
          class="btn"
          type="button"
          :disabled="settings.savingStorage"
          @click="settings.toggleAutoSave(!settings.autoSaveEnabled)"
        >
          {{ settings.autoSaveEnabled ? "关闭自动保存" : "开启自动保存" }}
        </button>
      </div>
    </div>

    <!-- 高级脱敏配置快照 -->
    <div class="band">
      <div class="panel-title" style="margin-bottom: 4px">
        <div>
          <h2>配置快照</h2>
          <span class="section-label">当前作用域脱敏只读 JSON</span>
        </div>
      </div>
      <details class="advanced-panel">
        <summary>展开生效配置 JSON</summary>
        <pre class="json" style="margin-top: 10px">{{ rawConfigJson }}</pre>
      </details>
    </div>
  </section>
</template>

<style scoped>
.view-settings {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.view-settings :deep(.band) {
  margin-top: 0;
}

.tabular-nums {
  font-variant-numeric: tabular-nums;
}

.dirty-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
  padding: 10px 14px;
  background: var(--amber-soft);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.dirty-bar-info {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  color: var(--ink);
}

.persist-info-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
  padding: 10px 14px;
  font-size: 13px;
  color: var(--text);
  background: var(--accent-soft);
  border: 1px solid var(--hairline-strong);
  border-radius: var(--radius-md);
}

.storage-status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

@media (max-width: 860px) {
  .storage-status-grid {
    grid-template-columns: 1fr;
  }
}
</style>
