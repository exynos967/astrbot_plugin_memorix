<script setup lang="ts">
// 图片记忆主视图（/images）：多模态图片画廊、核心指标、407deaf90 未配嵌入暂停引导、以图搜图、入库与详情治理。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import ImageDetailModal from "@/components/images/ImageDetailModal.vue";
import ImageJobsModal from "@/components/images/ImageJobsModal.vue";
import ImageSearchDrawer from "@/components/images/ImageSearchDrawer.vue";
import ImageUploadModal from "@/components/images/ImageUploadModal.vue";
import { useImagesStore } from "@/stores/images";

const store = useImagesStore();
const router = useRouter();

const uploadModalOpen = ref(false);
const galleryRef = ref<HTMLElement | null>(null);
let observer: IntersectionObserver | null = null;

const pendingTotal = computed(
  () =>
    (store.statusInfo?.stats?.pending_job_count ?? 0) +
    (store.statusInfo?.stats?.pending_description_count ?? 0),
);

const failedTotal = computed(
  () =>
    (store.statusInfo?.stats?.failed_job_count ?? 0) +
    (store.statusInfo?.stats?.failed_description_count ?? 0),
);

const indexPercent = computed(() => {
  const prog = store.statusInfo?.index_progress;
  if (!prog || !prog.total) return 100;
  return Math.min(100, Math.round((prog.ready / prog.total) * 100));
});

function goToImageMemorySettings(): void {
  void router.push({ path: "/settings", query: { group: "image_memory" } });
}

function setupLazyObserver(): void {
  if (observer) {
    observer.disconnect();
    observer = null;
  }
  const container = galleryRef.value;
  if (!container) return;

  if (typeof IntersectionObserver !== "function") {
    for (const item of store.assets) {
      void store.ensurePreview(item.asset_id);
    }
    return;
  }

  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const el = entry.target as HTMLElement;
        const assetId = el.dataset.assetId;
        if (assetId) {
          void store.ensurePreview(assetId);
          observer?.unobserve(el);
        }
      }
    },
    { rootMargin: "160px 0px" },
  );

  const cards = container.querySelectorAll<HTMLElement>("[data-asset-id]");
  cards.forEach((card) => observer?.observe(card));
}

watch(
  () => store.assets,
  async () => {
    await nextTick();
    setupLazyObserver();
  },
  { deep: false },
);

onMounted(() => {
  void store.refreshAll(true);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = null;
});

function formatShortTime(ts?: number | string): string {
  if (!ts) return "未记录日期";
  const num = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(String(ts));
  if (Number.isNaN(num)) return String(ts);
  return new Date(num).toLocaleDateString("zh-CN");
}

function quickSearchByAsset(assetId: string, contentHash: string): void {
  void store.runVisualSearch({
    contentHash,
    label: `参考图片 ${assetId.slice(0, 10)}`,
  });
}
</script>

<template>
  <section class="images-view">
    <!-- 上游 407deaf90 对齐：精准区分「未配多模态嵌入已暂停」与「已在设置中关闭」 -->
    <div
      v-if="store.isDisabled"
      class="notice"
      :class="store.disabledInfo.reason === 'unconfigured_embedding' ? 'warn' : ''"
    >
      <div class="banner-copy">
        <strong>{{ store.disabledInfo.title }}</strong>
        <p class="banner-sub">{{ store.disabledInfo.detail }}</p>
      </div>
      <button class="btn primary" type="button" @click="goToImageMemorySettings">
        {{ store.disabledInfo.ctaLabel }}
      </button>
    </div>

    <div
      v-else-if="store.statusInfo && store.runtimeStatus === 'exact_hash_only'"
      class="notice"
    >
      <div class="banner-copy">
        <strong>当前以同图精确哈希模式运行</strong>
        <p class="banner-sub">
          {{ store.statusInfo.message || "未配置图片嵌入模型，仅支持 SHA256 原图精确匹配；配置模型后可启用跨图视觉相似检索。" }}
        </p>
      </div>
      <button class="btn primary" type="button" @click="goToImageMemorySettings">
        配置图片嵌入模型
      </button>
    </div>

    <div
      v-else-if="store.statusInfo && store.runtimeStatus !== 'ready'"
      class="notice warn"
    >
      <div class="banner-progress-wrap">
        <strong>
          视觉索引状态：{{ store.runtimeStatus }}
          {{ store.statusInfo.message ? `— ${store.statusInfo.message}` : "" }}
        </strong>
        <div v-if="store.statusInfo.index_progress?.total" class="index-progress-box">
          <div class="progress-track">
            <div class="progress-fill" :style="{ width: `${indexPercent}%` }" />
          </div>
          <span class="section-label tabular">
            向量就绪 {{ store.statusInfo.index_progress.ready }} / {{ store.statusInfo.index_progress.total }} ({{ indexPercent }}%)
          </span>
        </div>
      </div>
      <button class="btn" type="button" @click="store.jobsModalOpen = true">
        查看任务队列
      </button>
    </div>

    <!-- 顶部三大核心指标卡（18px 线性矢量图标，零单字气泡） -->
    <div class="grid-3">
      <article class="metric">
        <div>
          <small>图片资产</small>
          <strong class="tabular">{{ store.statusInfo?.stats?.asset_count ?? 0 }}</strong>
          <p class="tabular">累计出现 {{ store.statusInfo?.stats?.occurrence_count ?? 0 }} 次</p>
        </div>
        <span class="icon-badge blue" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2.5" />
            <circle cx="8.5" cy="9.5" r="1.6" />
            <path d="m21 15-4.6-4.6a1.5 1.5 0 0 0-2.1 0L7 17.7" />
          </svg>
        </span>
      </article>

      <article class="metric">
        <div>
          <small>认知说明</small>
          <strong class="tabular">{{ store.statusInfo?.stats?.observation_count ?? 0 }}</strong>
          <p class="tabular">就绪视觉向量 {{ store.statusInfo?.stats?.ready_vector_count ?? 0 }} 条</p>
        </div>
        <span class="icon-badge teal" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" />
            <circle cx="12" cy="12" r="2.8" />
          </svg>
        </span>
      </article>

      <article class="metric">
        <div>
          <small>关联记忆</small>
          <strong class="tabular">{{ store.statusInfo?.stats?.link_count ?? 0 }}</strong>
          <p>绑定段落、实体、关系与情景</p>
        </div>
        <span class="icon-badge green" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l2.5-2.5a5 5 0 0 0-7.07-7.07l-1.5 1.5" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-2.5 2.5a5 5 0 0 0 7.07 7.07l1.5-1.5" />
          </svg>
        </span>
      </article>
    </div>

    <!-- 以图搜图面板 -->
    <ImageSearchDrawer />

    <!-- 画廊控制栏与展示区 -->
    <div class="band">
      <div class="panel-title">
        <div class="gallery-head-left">
          <h2>图片资产列表</h2>
          <button
            v-if="!store.isDisabled"
            type="button"
            class="tag queue-pill tabular"
            :class="failedTotal > 0 ? 'bad' : pendingTotal > 0 ? 'warn' : 'ok'"
            title="打开后台任务队列诊断"
            @click="store.jobsModalOpen = true"
          >
            {{
              failedTotal > 0
                ? `失败任务 ${failedTotal}`
                : pendingTotal > 0
                  ? `排队处理 ${pendingTotal}`
                  : "后台队列正常"
            }}
          </button>
        </div>

        <div class="gallery-toolbar">
          <select
            class="select chat-filter-select"
            :value="store.selectedChatId"
            :disabled="store.isDisabled"
            aria-label="按会话筛选图片"
            @change="store.setChatFilter(($event.target as HTMLSelectElement).value)"
          >
            <option value="">全部会话 ({{ store.statusInfo?.stats?.asset_count ?? 0 }})</option>
            <option v-for="c in store.chatStats" :key="c.chat_id" :value="c.chat_id">
              {{ c.chat_id }} ({{ c.asset_count }})
            </option>
          </select>

          <button
            class="btn"
            type="button"
            :disabled="store.isDisabled"
            @click="store.jobsModalOpen = true"
          >
            维护队列
          </button>
          <button
            class="btn"
            :class="{ 'btn-toggled': store.searchOpen }"
            type="button"
            :aria-pressed="store.searchOpen"
            :disabled="store.isDisabled"
            @click="store.searchOpen = !store.searchOpen"
          >
            以图搜图
          </button>
          <button class="btn" type="button" :disabled="store.loading" @click="store.refreshAll(false)">
            刷新列表
          </button>
          <button
            class="btn"
            :class="{ primary: !store.isDisabled }"
            type="button"
            :disabled="store.isDisabled"
            @click="uploadModalOpen = true"
          >
            上传图片
          </button>
        </div>
      </div>

      <div v-if="store.loading && !store.assets.length" class="gallery-grid">
        <div v-for="n in 8" :key="n" class="skeleton gallery-skeleton" />
      </div>

      <div v-else-if="store.assets.length" ref="galleryRef" class="gallery-grid">
        <article
          v-for="item in store.assets"
          :key="item.asset_id"
          :data-asset-id="item.asset_id"
          class="image-card card-interactive"
          role="button"
          tabindex="0"
          :aria-label="`查看图片资产 ${item.asset_id.slice(0, 10)}`"
          @click="store.openDetail(item.asset_id)"
          @keydown.enter.prevent="store.openDetail(item.asset_id)"
          @keydown.space.prevent="store.openDetail(item.asset_id)"
        >
          <div class="image-thumb-wrap">
            <img
              v-if="store.getPreviewUrl(item.asset_id)"
              :src="store.getPreviewUrl(item.asset_id)"
              :alt="`图片资产 ${item.asset_id.slice(0, 10)}`"
              loading="lazy"
            />
            <div v-else-if="store.previewFailed.has(item.asset_id)" class="thumb-fallback">
              原图未缓存
            </div>
            <div v-else class="skeleton thumb-loading" />

            <button
              v-if="item.content_hash"
              type="button"
              class="similar-quick-btn"
              title="按此图片检索相似资产与关联记忆"
              @click.stop="quickSearchByAsset(item.asset_id, item.content_hash)"
            >
              检索相似
            </button>
          </div>

          <div class="image-card-body">
            <div class="image-card-row">
              <strong class="mono">{{ item.asset_id.slice(0, 10) }}</strong>
              <span class="tag" :class="item.status === 'ready' ? 'ok' : 'warn'">
                {{ item.status === "ready" || !item.status ? "就绪" : item.status }}
              </span>
            </div>
            <div class="image-card-sub tabular">
              <span>出现 {{ item.occurrence_count ?? 1 }} 次</span>
              <span>{{ formatShortTime(item.latest_occurrence_at || item.created_at) }}</span>
            </div>
          </div>
        </article>
      </div>

      <div v-else-if="store.isDisabled" class="empty-state">
        <template v-if="store.disabledInfo.reason === 'unconfigured_embedding'">
          <strong>图片记忆已自动暂停</strong>
          <p>配置多模态图片嵌入模型（image_memory.openapi）后，即可恢复会话识图采集、手动上传与相似图片检索。</p>
        </template>
        <template v-else>
          <strong>已在设置中关闭图片记忆</strong>
          <p>在运行设置中启用图片记忆模块后即可在此浏览与治理图片资产。</p>
        </template>
      </div>

      <div v-else class="empty-state">
        <strong>当前作用域暂无图片资产</strong>
        <p>会话自动采集或点击「上传图片」入库的图片资产将在此展示。</p>
      </div>

      <div v-if="store.totalPages > 1" class="pagination-bar">
        <button
          class="btn sm"
          type="button"
          :disabled="store.page <= 1 || store.loading"
          @click="store.goToPage(store.page - 1)"
        >
          上一页
        </button>
        <span class="section-label tabular">
          第 {{ store.page }} / {{ store.totalPages }} 页（每页 24 张）
        </span>
        <button
          class="btn sm"
          type="button"
          :disabled="store.page >= store.totalPages || store.loading"
          @click="store.goToPage(store.page + 1)"
        >
          下一页
        </button>
      </div>
    </div>

    <ImageUploadModal :open="uploadModalOpen" @close="uploadModalOpen = false" />
    <ImageJobsModal :open="store.jobsModalOpen" @close="store.jobsModalOpen = false" />
    <ImageDetailModal />
  </section>
</template>

<style scoped>
.images-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.images-view > .band {
  margin-top: 0;
}

.tabular {
  font-variant-numeric: tabular-nums;
}

.banner-copy {
  flex: 1 1 320px;
  min-width: 0;
}

.banner-sub {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--muted);
}

.banner-progress-wrap {
  flex: 1 1 320px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.index-progress-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 440px;
}

.gallery-head-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.queue-pill {
  cursor: pointer;
}

.queue-pill:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.btn-toggled {
  color: var(--accent-ink);
  background: var(--accent-soft);
  border-color: var(--hairline-strong);
}

.gallery-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.chat-filter-select {
  width: auto;
  min-width: 164px;
  max-width: 240px;
}

.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(192px, 1fr));
  gap: 16px;
}

@media (max-width: 480px) {
  .gallery-grid {
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 12px;
  }
  .chat-filter-select {
    max-width: 100%;
    flex: 1 1 100%;
  }
}

.gallery-skeleton {
  height: 204px;
}

.image-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  overflow: hidden;
  cursor: pointer;
  transition:
    transform 180ms cubic-bezier(0.2, 0.8, 0.2, 1),
    border-color 150ms cubic-bezier(0.2, 0.8, 0.2, 1),
    box-shadow 180ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.image-card:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.image-thumb-wrap {
  position: relative;
  aspect-ratio: 4 / 3;
  background: var(--surface-soft);
  border-bottom: 1px solid var(--hairline);
  overflow: hidden;
}

.image-thumb-wrap img,
.thumb-loading {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.image-thumb-wrap img {
  transition: transform 400ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

@media (hover: hover) and (pointer: fine) {
  .image-card:hover {
    transform: translateY(-2px);
    border-color: var(--hairline-strong);
    box-shadow: var(--shadow-xs);
  }

  .image-card:hover .image-thumb-wrap img {
    transform: scale(1.03);
  }
}

@media (prefers-reduced-motion: reduce) {
  .image-card,
  .image-thumb-wrap img {
    transition: none;
  }
  .image-card:hover,
  .image-card:hover .image-thumb-wrap img {
    transform: none;
  }
}

.thumb-fallback {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  color: var(--muted);
  font-size: 12px;
}

.similar-quick-btn {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 4px 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--ink);
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
  opacity: 0;
  transition: opacity 150ms ease;
  box-shadow: var(--shadow-xs);
}

.image-card:hover .similar-quick-btn,
.image-card:focus-within .similar-quick-btn,
.similar-quick-btn:focus-visible {
  opacity: 1;
}

.similar-quick-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

@media (hover: none) {
  .similar-quick-btn {
    opacity: 1;
  }
}

.image-card-body {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.image-card-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.image-card-row strong {
  font-size: 12.5px;
  color: var(--ink);
}

.image-card-sub {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}

.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}
</style>
