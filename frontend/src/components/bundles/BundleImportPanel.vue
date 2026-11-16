<script setup lang="ts">
// 右上：上传 .amembundle 拖拽预检与确认导入面板。
import { computed, ref } from "vue";
import { useBundlesStore } from "@/stores/bundles";

const store = useBundlesStore();

const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);

const inspectCounts = computed(() => {
  const c = store.inspectResult?.counts;
  if (!c || typeof c !== "object") return [];
  const labelMap: Record<string, string> = {
    paragraphs: "段落",
    entities: "实体",
    relations: "关系",
    episodes: "事件",
    images: "图片",
    image_assets: "图片",
    vectors: "向量",
  };
  return Object.entries(c).map(([k, v]) => ({
    key: k,
    label: labelMap[k] || k,
    value: typeof v === "number" ? v : Number(v) || 0,
  }));
});

const missingResources = computed(
  () => store.inspectResult?.images?.missing_resources ?? [],
);

const textVecCompat = computed(() => {
  const r = store.inspectResult;
  if (!r) return null;
  if (typeof r.vector_compatible === "boolean") return r.vector_compatible;
  if (typeof r.vectors?.compatible === "boolean") return r.vectors.compatible;
  return null;
});

const imageVecCompat = computed(() => {
  const r = store.inspectResult;
  if (!r?.images) return null;
  if (typeof r.images.vector_compatible === "boolean") return r.images.vector_compatible;
  return null;
});

const canConfirmImport = computed(() => {
  if (store.importing || !store.uploadedPath) return false;
  if (store.importForm.scopeType === "chat" && !store.importForm.chatId.trim()) return false;
  return true;
});

function formatBytes(bytes?: number): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function handleFilePick(e: Event): void {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) {
    void store.uploadAndInspect(file);
  }
  if (fileInput.value) fileInput.value.value = "";
}

function handleDrop(e: DragEvent): void {
  dragging.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (file) {
    void store.uploadAndInspect(file);
  }
}
</script>

<template>
  <div class="band import-panel">
    <div class="panel-title">
      <div>
        <h2>上传与兼容性预检</h2>
        <span class="section-label">先核查知识包清单、向量指纹兼容性与缺失资源，再决定导入策略</span>
      </div>
      <button
        v-if="store.inspectResult"
        class="btn sm"
        type="button"
        @click="store.clearInspection()"
      >
        清除当前预检
      </button>
    </div>

    <input
      ref="fileInput"
      type="file"
      accept=".amembundle"
      style="display: none"
      @change="handleFilePick"
    />

    <div
      class="dropzone"
      :class="{ dragging }"
      role="button"
      tabindex="0"
      aria-label="选择或拖拽 .amembundle 知识包文件上传预检"
      @click="fileInput?.click()"
      @keydown.enter.prevent="fileInput?.click()"
      @keydown.space.prevent="fileInput?.click()"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="handleDrop"
    >
      <template v-if="store.uploading">
        <strong>正在上传并解析知识包清单…</strong>
        <span>大体积归档可能需要数秒完成校验</span>
      </template>
      <template v-else-if="store.uploadedFileName">
        <strong>已载入：{{ store.uploadedFileName }}</strong>
        <span>点击或拖拽新文件可替换当前预检结果</span>
      </template>
      <template v-else>
        <strong>点击选择或拖拽 .amembundle 知识包至此处</strong>
        <span>仅执行只读兼容性预检，不会立即写入数据库</span>
      </template>
    </div>

    <!-- 安装前兼容性预检区（扁平分割，不再套第三层框） -->
    <div v-if="store.inspectResult" class="inspect-section">
      <div class="inspect-header">
        <div>
          <h3>{{ store.inspectResult.package?.name || store.uploadedFileName }}</h3>
          <span class="section-label mono">
            {{ store.inspectResult.package?.id || "未命名包 ID" }} · v{{ store.inspectResult.package?.version || "1.0.0" }}
            <template v-if="store.inspectResult.package?.author">
              · {{ store.inspectResult.package.author }}
            </template>
          </span>
        </div>
        <div class="tags" style="margin-top: 0">
          <span class="tag info">{{ store.inspectResult.content_level || "knowledge" }}</span>
          <span class="tag tabular">{{ formatBytes(store.inspectResult.size) }}</span>
        </div>
      </div>

      <p v-if="store.inspectResult.package?.description" class="inspect-desc">
        {{ store.inspectResult.package.description }}
      </p>

      <div class="tags compat-tags">
        <span
          v-if="textVecCompat !== null"
          class="tag"
          :class="textVecCompat ? 'ok' : 'warn'"
        >
          {{ textVecCompat ? "文本向量兼容（免重算）" : "文本向量指纹不同（导入后重算）" }}
        </span>
        <span
          v-if="imageVecCompat !== null"
          class="tag"
          :class="imageVecCompat ? 'ok' : 'warn'"
        >
          {{ imageVecCompat ? "图片向量兼容" : "图片向量指纹不同（按需重编码）" }}
        </span>
        <span
          v-if="store.inspectResult.images?.runtime_status"
          class="tag"
          :class="store.inspectResult.images.runtime_status === 'ready' ? 'ok' : 'warn'"
        >
          图片引擎：{{ store.inspectResult.images.runtime_status }}
        </span>
      </div>

      <div v-if="missingResources.length" class="missing-alert">
        <strong class="tabular">缺失资源提醒（{{ missingResources.length }} 项）：</strong>
        <span>{{ missingResources.slice(0, 5).join("、") }}</span>
      </div>

      <div v-if="inspectCounts.length" class="inspect-counts">
        <div v-for="c in inspectCounts" :key="c.key" class="summary-item">
          <span>{{ c.label }}</span>
          <strong class="tabular">{{ c.value }}</strong>
        </div>
      </div>

      <div class="grid-2 import-options">
        <div class="field">
          <label for="bundle-imp-scope">目标作用域</label>
          <select id="bundle-imp-scope" v-model="store.importForm.scopeType" class="select">
            <option value="global">全局共享知识库（global）</option>
            <option value="chat">指定会话（chat）</option>
          </select>
        </div>
        <div class="field">
          <label for="bundle-imp-mode">合并策略</label>
          <select id="bundle-imp-mode" v-model="store.importForm.mode" class="select">
            <option value="merge">增量合并（merge · 推荐）</option>
            <option value="restore">快照还原（restore）</option>
          </select>
        </div>
      </div>

      <div v-if="store.importForm.scopeType === 'chat'" class="field">
        <label for="bundle-imp-chat">目标会话 Chat ID</label>
        <input
          id="bundle-imp-chat"
          v-model="store.importForm.chatId"
          class="input"
          placeholder="必填，例如 group_12345678"
        />
      </div>

      <div class="import-submit-bar">
        <button
          class="btn primary"
          type="button"
          :disabled="!canConfirmImport"
          @click="store.confirmImportBundle()"
        >
          {{ store.importing ? "正在导入…" : "导入知识包" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.import-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.inspect-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
}

.inspect-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.inspect-header h3 {
  margin: 0;
  font-size: 15px;
  color: var(--ink);
}

.inspect-desc {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.5;
}

.compat-tags {
  margin-top: 0;
}

.missing-alert {
  padding: 9px 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  background: var(--amber-soft);
  color: var(--amber);
  font-size: 12.5px;
  overflow-wrap: anywhere;
}

.inspect-counts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(92px, 1fr));
  gap: 8px;
}

.import-options {
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
}

.import-submit-bar {
  display: flex;
  justify-content: flex-end;
}
</style>
