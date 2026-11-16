<script setup lang="ts">
// 以图搜图面板：支持上传参考图片或基于已有图片的 content_hash 发起相似检索，
// 区分“原图匹配 (exact_hash)”与“视觉相似 (visual_similar)”，兼容未配向量时的精确哈希检索。
import { ref } from "vue";
import { useImagesStore } from "@/stores/images";

const store = useImagesStore();

const fileInput = ref<HTMLInputElement | null>(null);
const queryPreviewUrl = ref("");

function onPickQueryImage(e: Event): void {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file || !file.type.startsWith("image/")) return;
  const reader = new FileReader();
  reader.onload = () => {
    const res = typeof reader.result === "string" ? reader.result : "";
    queryPreviewUrl.value = res;
    const idx = res.indexOf(",");
    const b64 = idx >= 0 ? res.slice(idx + 1) : res;
    void store.runVisualSearch({
      imageBase64: b64,
      label: `参考文件：${file.name}`,
    });
  };
  reader.readAsDataURL(file);
}

function rerunCurrentSearch(): void {
  void store.runVisualSearch({});
}

function formatMs(val: unknown): string {
  if (typeof val !== "number" || Number.isNaN(val)) return "-";
  return `${val.toFixed(1)} ms`;
}
</script>

<template>
  <section v-if="store.searchOpen" class="band search-drawer">
    <div class="panel-title">
      <div>
        <h2>以图搜图</h2>
        <span class="section-label">
          {{ store.searchSourceLabel || "选择参考图或在资产卡片点击「检索相似」查找同源及相似图片" }}
        </span>
      </div>
      <button class="btn ghost sm" type="button" @click="store.searchOpen = false">收起面板</button>
    </div>

    <input
      ref="fileInput"
      type="file"
      accept="image/*"
      style="display: none"
      @change="onPickQueryImage"
    />

    <div class="search-controls">
      <button class="btn primary" type="button" :disabled="store.searching" @click="fileInput?.click()">
        选择参考图片
      </button>

      <img
        v-if="queryPreviewUrl && !store.searchHash"
        :src="queryPreviewUrl"
        alt="查询参考图片"
        class="query-thumb"
      />

      <div class="field threshold-field">
        <label for="image-similarity-range">
          相似度阈值：<strong class="mono tabular">{{ store.similarityThreshold.toFixed(2) }}</strong>
        </label>
        <input
          id="image-similarity-range"
          v-model.number="store.similarityThreshold"
          type="range"
          min="0.4"
          max="0.98"
          step="0.01"
        />
      </div>

      <button
        class="btn"
        type="button"
        :disabled="store.searching || (!store.searchHash && !queryPreviewUrl)"
        @click="rerunCurrentSearch"
      >
        {{ store.searching ? "检索中…" : "重新检索" }}
      </button>
    </div>

    <div
      v-if="store.searchStatus === 'unavailable' || !store.embeddingConfigured"
      class="tags timings-bar"
    >
      <span class="tag warn">未配置图片嵌入模型：当前仅执行 SHA256 原图精确匹配</span>
    </div>

    <div v-if="store.searchTimings" class="tags timings-bar tabular">
      <span class="tag info">总耗时 {{ formatMs(store.searchTimings.total) }}</span>
      <span v-if="store.searchTimings.embedding !== undefined" class="tag">
        向量编码 {{ formatMs(store.searchTimings.embedding) }}
      </span>
      <span v-if="store.searchTimings.vector_search !== undefined" class="tag">
        近邻检索 {{ formatMs(store.searchTimings.vector_search) }}
      </span>
      <span v-if="store.searchTimings.expansion !== undefined" class="tag">
        关联扩展 {{ formatMs(store.searchTimings.expansion) }}
      </span>
    </div>

    <div v-if="store.searching" class="empty-state">
      <strong>正在匹配图像特征与关联记忆…</strong>
    </div>
    <div v-else-if="store.searchHits.length" class="search-hits-grid">
      <article
        v-for="hit in store.searchHits"
        :key="hit.asset_id"
        class="hit-card card-interactive"
        role="button"
        tabindex="0"
        @click="store.openDetail(hit.asset_id)"
        @keydown.enter.prevent="store.openDetail(hit.asset_id)"
        @keydown.space.prevent="store.openDetail(hit.asset_id)"
      >
        <div class="hit-thumb-box">
          <img
            v-if="store.getPreviewUrl(hit.asset_id)"
            :src="store.getPreviewUrl(hit.asset_id)"
            :alt="`命中图片 ${hit.asset_id.slice(0, 10)}`"
          />
          <div v-else class="skeleton hit-thumb-placeholder" />
        </div>
        <div class="hit-info">
          <div class="hit-badges tabular">
            <span
              class="tag"
              :class="hit.match_kind === 'exact_hash' ? 'ok' : 'teal'"
            >
              {{ hit.match_kind === "exact_hash" ? "原图匹配" : "视觉相似" }}
            </span>
            <span class="tag mono">{{ (hit.similarity * 100).toFixed(1) }}%</span>
          </div>
          <p class="hit-obs">
            {{
              hit.observations?.[0]?.text ||
              hit.related_memories?.[0]?.content ||
              `资产 ${hit.asset_id.slice(0, 12)}`
            }}
          </p>
          <div class="hit-meta tabular">
            <span>出现 {{ hit.occurrences?.length ?? 0 }} 次</span>
            <span>关联 {{ hit.related_memories?.length ?? 0 }} 条</span>
          </div>
        </div>
      </article>
    </div>
    <div v-else-if="store.searchSourceLabel" class="empty-state">
      <strong>未检索到高于阈值 {{ store.similarityThreshold.toFixed(2) }} 的匹配图片</strong>
      <p>可调低相似度阈值后点击「重新检索」。</p>
    </div>
  </section>
</template>

<style scoped>
.search-drawer {
  margin-top: 0;
  border-color: var(--hairline-strong);
}

.tabular {
  font-variant-numeric: tabular-nums;
}

.search-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.query-thumb {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-sm);
  object-fit: cover;
  border: 1px solid var(--border-color);
}

.threshold-field {
  flex: 1 1 200px;
  max-width: 280px;
}

.threshold-field input[type="range"] {
  width: 100%;
  accent-color: var(--accent);
}

.timings-bar {
  margin-bottom: 12px;
}

.search-hits-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 12px;
}

.hit-card {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr);
  gap: 12px;
  padding: 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  cursor: pointer;
  transition:
    border-color 150ms cubic-bezier(0.2, 0.8, 0.2, 1),
    transform 180ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.hit-card:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.hit-thumb-box {
  width: 80px;
  height: 80px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--surface-soft);
  border: 1px solid var(--hairline);
}

.hit-thumb-box img,
.hit-thumb-placeholder {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.hit-thumb-box img {
  transition: transform 400ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

@media (hover: hover) and (pointer: fine) {
  .hit-card:hover {
    border-color: var(--hairline-strong);
    transform: translateY(-2px);
  }
  .hit-card:hover .hit-thumb-box img {
    transform: scale(1.03);
  }
}

@media (prefers-reduced-motion: reduce) {
  .hit-card,
  .hit-thumb-box img {
    transition: none;
  }
  .hit-card:hover,
  .hit-card:hover .hit-thumb-box img {
    transform: none;
  }
}

.hit-info {
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 4px;
}

.hit-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.hit-obs {
  margin: 0;
  font-size: 12.5px;
  color: var(--text);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.hit-meta {
  display: flex;
  gap: 10px;
  font-size: 11.5px;
  color: var(--muted);
}
</style>
