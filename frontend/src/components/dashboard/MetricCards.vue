<script setup lang="ts">
// 顶部四块核心指标卡：图谱实体 / 记忆关系 / 向量索引（双池拆解） / 图片记忆与健康度（对齐上游 407deaf90）。
// 使用 18px 线性矢量图标与扁平语义徽标，零单字气泡、零紫红/品红渐变。
import { computed } from "vue";
import NavIcons from "@/components/shell/NavIcons.vue";
import type { RuntimeReport } from "@/services/configApi";
import { classifyImageMemoryDisabled } from "@/services/imageApi";
import type { ImageMemoryStatus } from "@/services/memoryAdminApi";
import { deriveVectorSummary, runtimeLabel } from "@/utils/dashboardText";

const props = defineProps<{
  nodes: number | null;
  edges: number | null;
  vectors: number | null;
  dimension?: number | null;
  runtime?: RuntimeReport | null;
  imageStatus?: ImageMemoryStatus | null;
}>();

const emit = defineEmits<{
  navigate: [path: string];
  "configure-image": [];
}>();

function display(value: number | null | undefined): string {
  return value == null ? "-" : String(value);
}

const vectorSummary = computed(() =>
  deriveVectorSummary(props.vectors, props.dimension, props.runtime),
);

const runtimeChip = computed(() => runtimeLabel(props.runtime));

const imageDisabled = computed(() => classifyImageMemoryDisabled(props.imageStatus));

const fourthCardValue = computed(() => {
  if (props.imageStatus) {
    if (imageDisabled.value.disabled) {
      const count = Number(props.imageStatus.asset_count ?? 0);
      if (count > 0) return String(count);
      return imageDisabled.value.reason === "unconfigured_embedding" ? "已暂停" : "已关闭";
    }
    return String(props.imageStatus.asset_count ?? 0);
  }
  return runtimeChip.value[0];
});

const fourthCardNote = computed(() => {
  const healthText = `检索${runtimeChip.value[0]}`;
  if (!props.imageStatus) {
    return vectorSummary.value.dimension
      ? `${healthText} · ${vectorSummary.value.dimension}d`
      : `${healthText} · 多模态状态待载入`;
  }
  if (imageDisabled.value.disabled) {
    return imageDisabled.value.reason === "unconfigured_embedding"
      ? `未配图片嵌入（防报错暂停） · 点击配置`
      : `已在设置中关闭图片记忆 · ${healthText}`;
  }
  if (props.imageStatus.status === "exact_hash_only") {
    return `同图精确哈希模式 · ${healthText}`;
  }
  const readyVec = Number(props.imageStatus.ready_vector_count ?? 0);
  const pending = Number(props.imageStatus.pending_job_count ?? 0);
  return pending > 0
    ? `向量就绪 ${readyVec} · 待处理 ${pending} · ${healthText}`
    : `向量就绪 ${readyVec} · ${healthText}`;
});

const fourthIconTone = computed(() => {
  if (imageDisabled.value.disabled) {
    return imageDisabled.value.reason === "unconfigured_embedding" ? "amber" : "slate";
  }
  return "green";
});

function onFourthCardClick(): void {
  if (imageDisabled.value.disabled && imageDisabled.value.reason === "unconfigured_embedding") {
    emit("configure-image");
    return;
  }
  emit("navigate", "/images");
}
</script>

<template>
  <div class="metric-grid-4">
    <article
      class="metric metric-link"
      role="button"
      tabindex="0"
      aria-label="前往知识图谱查看实体节点"
      @click="emit('navigate', '/graph')"
      @keydown.enter.prevent="emit('navigate', '/graph')"
      @keydown.space.prevent="emit('navigate', '/graph')"
    >
      <div>
        <small>图谱实体</small>
        <strong>{{ display(nodes) }}</strong>
        <p>实体节点总量</p>
      </div>
      <span class="icon-badge blue" aria-hidden="true">
        <NavIcons name="entity" :size="18" />
      </span>
    </article>

    <article
      class="metric metric-link"
      role="button"
      tabindex="0"
      aria-label="前往知识图谱查看关系边"
      @click="emit('navigate', '/graph')"
      @keydown.enter.prevent="emit('navigate', '/graph')"
      @keydown.space.prevent="emit('navigate', '/graph')"
    >
      <div>
        <small>记忆关系</small>
        <strong>{{ display(edges) }}</strong>
        <p>活跃与受保护关系边</p>
      </div>
      <span class="icon-badge teal" aria-hidden="true">
        <NavIcons name="relation" :size="18" />
      </span>
    </article>

    <article
      class="metric metric-link"
      role="button"
      tabindex="0"
      aria-label="前往记忆与事实维护向量索引"
      @click="emit('navigate', '/memory')"
      @keydown.enter.prevent="emit('navigate', '/memory')"
      @keydown.space.prevent="emit('navigate', '/memory')"
    >
      <div>
        <small>向量索引</small>
        <strong>{{ display(vectorSummary.total) }}</strong>
        <p>{{ vectorSummary.subtitle }}</p>
      </div>
      <span class="icon-badge blue" aria-hidden="true">
        <NavIcons name="vector" :size="18" />
      </span>
    </article>

    <article
      class="metric metric-link"
      role="button"
      tabindex="0"
      :aria-label="
        imageDisabled.disabled && imageDisabled.reason === 'unconfigured_embedding'
          ? '配置图片嵌入模型'
          : '前往图片记忆'
      "
      @click="onFourthCardClick"
      @keydown.enter.prevent="onFourthCardClick"
      @keydown.space.prevent="onFourthCardClick"
    >
      <div>
        <small>图片记忆 · 健康度</small>
        <strong :class="{ 'metric-text-value': isNaN(Number(fourthCardValue)) }">
          {{ fourthCardValue }}
        </strong>
        <p>{{ fourthCardNote }}</p>
      </div>
      <span class="icon-badge" :class="fourthIconTone" aria-hidden="true">
        <NavIcons name="images" :size="18" />
      </span>
    </article>
  </div>
</template>

<style scoped>
.metric-grid-4 {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.metric-text-value {
  font-size: 23px;
  line-height: 1.2;
}

@media (max-width: 1120px) {
  .metric-grid-4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 580px) {
  .metric-grid-4 {
    grid-template-columns: 1fr;
  }
}
</style>
