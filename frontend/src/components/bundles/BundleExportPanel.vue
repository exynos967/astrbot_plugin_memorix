<script setup lang="ts">
// 左侧：导出记忆包面板（.amembundle）—— 元数据、内容层级、多模式选择器、规模预览与下载。
import { computed } from "vue";
import { useBundlesStore } from "@/stores/bundles";

const store = useBundlesStore();

const previewCounts = computed(() => {
  const c = store.previewData?.counts;
  if (!c || typeof c !== "object") return [];
  const labelMap: Record<string, string> = {
    paragraphs: "记忆段落",
    entities: "图谱实体",
    relations: "关系边",
    episodes: "事件摘要",
    images: "图片资产",
    image_assets: "图片资产",
    image_occurrences: "图片出现",
    observations: "视觉认知",
    vectors: "向量条目",
  };
  return Object.entries(c).map(([k, v]) => ({
    key: k,
    label: labelMap[k] || k,
    value: typeof v === "number" ? v : Number(v) || 0,
  }));
});

function formatBytes(bytes?: number): string {
  if (bytes === undefined || bytes === null) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
</script>

<template>
  <div class="band export-panel">
    <div class="panel-title">
      <div>
        <h2>导出知识包</h2>
        <span class="section-label">将图谱、段落、事件与图片记忆打包为便携归档（.amembundle）</span>
      </div>
    </div>

    <!-- 1. 包元数据 -->
    <div class="form-section first-section">
      <h3 class="sub-heading">1. 知识包元数据</h3>
      <div class="grid-2">
        <div class="field">
          <label for="bundle-exp-name">知识包名称</label>
          <input id="bundle-exp-name" v-model="store.exportForm.name" class="input" placeholder="例如：群聊核心设定集" />
        </div>
        <div class="field">
          <label for="bundle-exp-version">版本号</label>
          <input id="bundle-exp-version" v-model="store.exportForm.version" class="input mono" placeholder="1.0.0" />
        </div>
        <div class="field">
          <label for="bundle-exp-author">作者 / 维护者</label>
          <input id="bundle-exp-author" v-model="store.exportForm.author" class="input" placeholder="可选" />
        </div>
        <div class="field">
          <label for="bundle-exp-pkgid">包标识符（留空自动生成）</label>
          <input id="bundle-exp-pkgid" v-model="store.exportForm.packageId" class="input mono" placeholder="pkg.custom.id（可选）" />
        </div>
      </div>
      <div class="field" style="margin-top: 8px">
        <label for="bundle-exp-desc">内容简介</label>
        <textarea
          id="bundle-exp-desc"
          v-model="store.exportForm.description"
          class="textarea"
          rows="2"
          style="min-height: 68px"
          placeholder="简要说明用途与覆盖范围…"
        />
      </div>
    </div>

    <!-- 2. 导出层级与附加选项 -->
    <div class="form-section">
      <h3 class="sub-heading">2. 内容层级与多模态选项</h3>
      <div class="field">
        <label>打包内容层级</label>
        <div class="segmented" role="group" aria-label="打包内容层级">
          <button
            type="button"
            :class="{ active: store.exportForm.contentLevel === 'knowledge' }"
            @click="store.exportForm.contentLevel = 'knowledge'"
          >
            精简知识层（knowledge）
          </button>
          <button
            type="button"
            :class="{ active: store.exportForm.contentLevel === 'full' }"
            @click="store.exportForm.contentLevel = 'full'"
          >
            完整溯源存档（full）
          </button>
        </div>
      </div>

      <div class="check-grid">
        <label class="check-item">
          <input v-model="store.exportForm.includeVectors" type="checkbox" />
          <span>包含预计算向量索引（同模型免重算）</span>
        </label>
        <label class="check-item">
          <input v-model="store.exportForm.includeImages" type="checkbox" />
          <span>包含原始图片资产文件</span>
        </label>
        <label class="check-item">
          <input v-model="store.exportForm.includeImageRelated" type="checkbox" />
          <span>包含图片认知说明与记忆关联</span>
        </label>
      </div>
    </div>

    <!-- 3. 多模式范围选择器 -->
    <div class="form-section">
      <h3 class="sub-heading">3. 筛选范围</h3>
      <div class="field">
        <label for="bundle-selector-type">筛选方式</label>
        <select id="bundle-selector-type" v-model="store.exportForm.selectorType" class="select">
          <option value="all">当前作用域全量导出（all）</option>
          <option value="chat">按指定会话导出（chat）</option>
          <option value="source">按来源列表导出（source）</option>
          <option value="package">按已安装知识包导出（package）</option>
          <option value="import_task">按导入任务导出（import_task）</option>
          <option value="image">按图片内容哈希导出（image）</option>
        </select>
      </div>

      <div v-if="store.exportForm.selectorType === 'chat'" class="field selector-sub">
        <label for="bundle-sel-chat">目标会话 Chat ID</label>
        <input id="bundle-sel-chat" v-model="store.exportForm.selectorChatId" class="input" placeholder="例如：group_987654321" />
      </div>

      <div v-else-if="store.exportForm.selectorType === 'source'" class="field selector-sub">
        <label for="bundle-sel-sources">来源标识列表（每行或逗号分隔）</label>
        <textarea
          id="bundle-sel-sources"
          v-model="store.exportForm.selectorSourcesText"
          class="textarea"
          rows="2"
          style="min-height: 68px"
          placeholder="填写一个或多个 source 标识…"
        />
      </div>

      <div v-else-if="store.exportForm.selectorType === 'package'" class="grid-2 selector-sub">
        <div class="field">
          <label for="bundle-sel-inst">安装记录</label>
          <select id="bundle-sel-inst" v-model="store.exportForm.selectorInstallationId" class="select">
            <option value="">从已安装包选择…</option>
            <option
              v-for="pkg in store.installedItems"
              :key="pkg.installation_id"
              :value="pkg.installation_id"
            >
              {{ pkg.name || pkg.package_id }} ({{ pkg.version }})
            </option>
          </select>
        </div>
        <div class="field">
          <label for="bundle-sel-pkg">或填写 Package ID</label>
          <input id="bundle-sel-pkg" v-model="store.exportForm.selectorPackageId" class="input mono" placeholder="可选" />
        </div>
      </div>

      <div v-else-if="store.exportForm.selectorType === 'import_task'" class="field selector-sub">
        <label for="bundle-sel-task">导入任务 ID</label>
        <input id="bundle-sel-task" v-model="store.exportForm.selectorTaskId" class="input mono" placeholder="输入导入任务 task_id" />
      </div>

      <div v-else-if="store.exportForm.selectorType === 'image'" class="field selector-sub">
        <label for="bundle-sel-imgs">图片 SHA256 哈希列表（每行或逗号分隔）</label>
        <textarea
          id="bundle-sel-imgs"
          v-model="store.exportForm.selectorImageHashesText"
          class="textarea mono"
          rows="2"
          style="min-height: 68px"
          placeholder="输入一个或多个图片 content_hash…"
        />
      </div>
    </div>

    <!-- 预览导出规模（扁平分割区，避免三层盒套盒） -->
    <div v-if="store.previewData" class="preview-section">
      <div class="preview-head">
        <strong>导出规模预览</strong>
        <span class="tag info tabular-nums">
          预估体积 {{ formatBytes(store.previewData.size ?? store.previewData.uncompressed_size) }}
        </span>
      </div>
      <div v-if="previewCounts.length" class="preview-counts">
        <div v-for="item in previewCounts" :key="item.key" class="summary-item">
          <span>{{ item.label }}</span>
          <strong class="tabular-nums">{{ item.value }}</strong>
        </div>
      </div>
    </div>

    <div class="export-actions">
      <button
        class="btn"
        type="button"
        :disabled="store.previewLoading || store.exporting"
        @click="store.runExportPreview()"
      >
        {{ store.previewLoading ? "正在估算…" : "估算导出规模" }}
      </button>
      <button
        v-if="store.lastExportedFile"
        class="btn"
        type="button"
        :disabled="store.exporting"
        @click="store.redownloadLast()"
      >
        重新下载最近包
      </button>
      <button
        class="btn primary"
        type="button"
        :disabled="store.exporting"
        @click="store.exportAndDownload()"
      >
        {{ store.exporting ? "打包下载中…" : "导出并下载知识包" }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.tabular-nums {
  font-variant-numeric: tabular-nums;
}

.export-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.form-section.first-section {
  padding-top: 0;
  border-top: 0;
}

.sub-heading {
  margin: 0;
  font-size: 13.5px;
  font-weight: 680;
  color: var(--ink);
}

.check-grid {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 4px;
}

.check-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
}

.check-item input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
}

.selector-sub {
  margin-top: 4px;
}

.preview-section {
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.preview-head strong {
  font-size: 13.5px;
  color: var(--ink);
}

.preview-counts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(105px, 1fr));
  gap: 8px;
}

.export-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--hairline);
}
</style>
