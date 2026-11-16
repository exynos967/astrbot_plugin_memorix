<script setup lang="ts">
// 运行概况卡片：展示 Schema v31 运行时健康、Embedding 维度、双池向量就绪态、自动保存与图片记忆门控状态（407deaf90）。
import { computed, ref } from "vue";
import { classifyImageMemoryDisabled } from "@/services/imageApi";
import { useDashboardStore } from "@/stores/dashboard";
import {
  deriveVectorSummary,
  extractVectorPools,
  runtimeLabel,
  runtimeMessageText,
} from "@/utils/dashboardText";

const emit = defineEmits<{
  "configure-image": [];
}>();

const dashboard = useDashboardStore();
const checking = ref(false);

const chip = computed(() => runtimeLabel(dashboard.runtime));
const chipLabel = computed(() => chip.value[0]);
const chipToneClass = computed(() => {
  const t = chip.value[1];
  return t === "ok" ? "ok" : t === "bad" ? "bad" : "warn";
});

const pools = computed(() => extractVectorPools(dashboard.runtime));
const vectorInfo = computed(() =>
  deriveVectorSummary(
    dashboard.stats?.vector_store.num_vectors,
    dashboard.stats?.vector_store.dimension,
    dashboard.runtime,
  ),
);

const poolTagText = computed(() => {
  if (pools.value?.effective_mode === "dual") {
    return `双池向量：段落 ${vectorInfo.value.paragraphVectors} / 图谱 ${vectorInfo.value.graphVectors}`;
  }
  if (pools.value?.effective_mode === "single") {
    return `单池向量：${vectorInfo.value.total ?? 0} 条`;
  }
  return vectorInfo.value.total != null
    ? `向量索引：${vectorInfo.value.total} 条`
    : "向量池：待检测";
});

const poolTagTone = computed(() => {
  if (!pools.value) return "";
  return pools.value.ready !== false ? "ok" : "warn";
});

const dimTagText = computed(() =>
  vectorInfo.value.dimension ? `Embedding ${vectorInfo.value.dimension} 维` : "Embedding 维度待检",
);

const autoSaveText = computed(() =>
  dashboard.config?.auto_save_enabled ? "自动保存：开启" : "自动保存：关闭",
);
const autoSaveTone = computed(() => (dashboard.config?.auto_save_enabled ? "ok" : "warn"));

const imageDisabled = computed(() => classifyImageMemoryDisabled(dashboard.imageStatus));

const imageTagText = computed(() => {
  const img = dashboard.imageStatus;
  if (!img) return "图片记忆：按需就绪";
  if (imageDisabled.value.disabled) {
    return imageDisabled.value.reason === "unconfigured_embedding"
      ? "图片记忆：未配置图片嵌入模型，已暂停（防识图报错）"
      : "图片记忆：已在设置中关闭";
  }
  if (img.status === "exact_hash_only") {
    return `图片记忆：${img.asset_count ?? 0} 张（同图精确哈希模式）`;
  }
  return `图片记忆：${img.asset_count ?? 0} 张（就绪向量 ${img.ready_vector_count ?? 0}）`;
});

const imageTagTone = computed(() => {
  const img = dashboard.imageStatus;
  if (!img) return "";
  if (imageDisabled.value.disabled) {
    return imageDisabled.value.reason === "unconfigured_embedding" ? "warn" : "";
  }
  if (img.status === "exact_hash_only") return "info";
  return Number(img.failed_job_count ?? 0) > 0 ? "warn" : "ok";
});

const runtimeSummary = computed(() =>
  runtimeMessageText(dashboard.runtime, "正在同步运行时自检状态…"),
);

async function forceCheck(): Promise<void> {
  if (checking.value) return;
  checking.value = true;
  try {
    await dashboard.loadRuntime(true);
  } finally {
    checking.value = false;
  }
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div class="overview-heading">
        <h2>运行概况</h2>
        <span class="section-label">{{ runtimeSummary }}</span>
      </div>
      <div class="overview-actions">
        <span class="tag" :class="chipToneClass">运行时：{{ chipLabel }}</span>
        <button
          v-if="imageDisabled.disabled && imageDisabled.reason === 'unconfigured_embedding'"
          class="btn sm"
          type="button"
          @click="emit('configure-image')"
        >
          配置图片嵌入模型
        </button>
        <button
          class="btn sm"
          type="button"
          :disabled="checking"
          @click="forceCheck"
        >
          {{ checking ? "自检中…" : "重新自检" }}
        </button>
      </div>
    </div>
    <div class="runtime-grid">
      <span class="tag ok">控制台已鉴权</span>
      <span class="tag ok">Schema v31 存储契约</span>
      <span class="tag" :class="poolTagTone">{{ poolTagText }}</span>
      <span class="tag" :class="vectorInfo.dimension ? 'ok' : ''">{{ dimTagText }}</span>
      <span class="tag" :class="autoSaveTone">{{ autoSaveText }}</span>
      <span class="tag" :class="imageTagTone">{{ imageTagText }}</span>
    </div>
  </div>
</template>

<style scoped>
.overview-heading {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.overview-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
