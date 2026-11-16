<script setup lang="ts">
// 导入任务创建面板：
// 1. mode === "text" 始终保持原始字符串提交，严禁 JSON.parse 破坏 JSON 聊天记录原文。
// 2. 支持拖拽多文件上传、工作区目录扫描（create_raw_scan）及结构化直写。
// 3. 扁平化分区结构（不超过 2 层容器嵌套），用户显式 Options JSON 优先于表单默认值。
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import { useTaskStore } from "@/stores/task";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import type { CandidateItem } from "@/stores/candidate";
import type {
  ImportMode,
  KnowledgeType,
  RawScanAlias,
} from "@/services/taskApi";

type UiImportMode = ImportMode | "upload";

const KNOWLEDGE_TYPE_OPTIONS: { value: KnowledgeType; label: string }[] = [
  { value: "auto", label: "自动识别（auto）" },
  { value: "narrative", label: "叙事记忆（narrative）" },
  { value: "factual", label: "事实知识（factual）" },
  { value: "structured", label: "结构化知识（structured）" },
  { value: "mixed", label: "混合内容（mixed）" },
];

const store = useTaskStore();
const graph = useGraphStore();
const app = useAppStore();
const { creating, uploadProgressText } = storeToRefs(store);

// 模式与通用参数
const mode = ref<UiImportMode>("text");
const knowledgeType = ref<KnowledgeType>("auto");
const source = ref("web_import");
const chatLog = ref(false);
const windowSize = ref(1600);
const overlap = ref(400);
const payload = ref("");
const optionsText = ref("{}");

// 多文件拖拽上传状态
const files = ref<File[]>([]);
const isDragOver = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);

// 工作区目录扫描参数（create_raw_scan）
const scanAlias = ref<RawScanAlias>("raw");
const scanRelativePath = ref("");
const scanGlob = ref("*");
const scanRecursive = ref(true);

const sourceInput = ref<HTMLInputElement | null>(null);

function sourceCandidates(keyword: string): CandidateItem[] {
  const kw = keyword.trim().toLowerCase();
  return graph.nodeLabels
    .filter((label) => !kw || label.toLowerCase().includes(kw))
    .slice(0, 20)
    .map((value) => ({ value, kind: "节点" }));
}

const sourceMenu = useCandidateMenu({
  inputRef: sourceInput,
  model: source,
  source: sourceCandidates,
  debounceMs: 0,
});

const supportsChatLog = computed(() =>
  mode.value === "text" || mode.value === "upload" || mode.value === "file",
);

const supportsKnowledgeType = computed(() =>
  mode.value === "text" || mode.value === "upload" || mode.value === "file" || mode.value === "json",
);

const activeAliasPathHint = computed(
  () => store.pathAliases[scanAlias.value] || "",
);

const canSubmit = computed(() => {
  if (creating.value) return false;
  if (mode.value === "upload") return files.value.length > 0;
  if (mode.value === "file") return Boolean(scanAlias.value && scanGlob.value.trim());
  return Boolean(payload.value.trim());
});

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(2)} MB`;
}

function fileSuffixBadge(name: string): string {
  const idx = name.lastIndexOf(".");
  return idx >= 0 ? name.slice(idx + 1).toUpperCase() : "FILE";
}

function appendFiles(incoming: FileList | File[] | null | undefined): void {
  if (!incoming) return;
  const existingKeys = new Set(
    files.value.map((f) => `${f.name}::${f.size}::${f.lastModified}`),
  );
  const next = [...files.value];
  for (const f of Array.from(incoming)) {
    const key = `${f.name}::${f.size}::${f.lastModified}`;
    if (!existingKeys.has(key)) {
      existingKeys.add(key);
      next.push(f);
    }
  }
  files.value = next;
}

function onSelectFiles(event: Event): void {
  const target = event.target as HTMLInputElement;
  appendFiles(target.files);
  target.value = "";
}

function onDropFiles(event: DragEvent): void {
  isDragOver.value = false;
  appendFiles(event.dataTransfer?.files);
}

function removeFile(index: number): void {
  files.value = files.value.filter((_, i) => i !== index);
}

function clearFiles(): void {
  files.value = [];
}

function parseUserOptionsOverride(): Record<string, unknown> | null {
  const raw = optionsText.value.trim();
  if (!raw || raw === "{}") return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      app.pushError("高级 Options JSON 必须是 JSON 对象（例如 {\"batch_size\": 8}）", "导入配置");
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch (err) {
    app.pushError(
      `高级 Options JSON 语法错误：${err instanceof Error ? err.message : "格式无效"}`,
      "导入配置",
    );
    return null;
  }
}

function buildMergedOptions(userOverrides: Record<string, unknown>): Record<string, unknown> {
  const base: Record<string, unknown> = {};
  const src = source.value.trim();
  if (src) base.source = src;

  if (supportsKnowledgeType.value) {
    base.knowledge_type = knowledgeType.value;
  }

  if (supportsChatLog.value) {
    base.chat_log = chatLog.value;
    if (chatLog.value) {
      const safeWin = Math.max(200, Math.min(100000, Number(windowSize.value) || 1600));
      const safeOverlap = Math.max(0, Math.min(safeWin - 1, Number(overlap.value) || 400));
      base.narrative_window_size = safeWin;
      base.narrative_overlap = safeOverlap;
    }
  }

  return { ...base, ...userOverrides };
}

function resolvePayloadForMode(): { ok: true; value: unknown } | { ok: false } {
  const raw = payload.value;
  const trimmed = raw.trim();
  if (!trimmed) {
    app.pushError("请填写要导入的内容", "导入任务");
    return { ok: false };
  }

  if (mode.value === "text") {
    return { ok: true, value: raw };
  }

  if (mode.value === "json") {
    try {
      return { ok: true, value: JSON.parse(trimmed) };
    } catch (err) {
      app.pushError(
        `JSON 内容解析失败：${err instanceof Error ? err.message : "请检查 JSON 格式"}`,
        "导入任务",
      );
      return { ok: false };
    }
  }

  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return { ok: true, value: JSON.parse(trimmed) };
    } catch {
      return { ok: true, value: raw };
    }
  }
  return { ok: true, value: raw };
}

async function onCreate(): Promise<void> {
  const userOverrides = parseUserOptionsOverride();
  if (userOverrides === null) return;
  const options = buildMergedOptions(userOverrides);

  if (mode.value === "upload") {
    const ok = await store.createUploads(files.value, options);
    if (ok) files.value = [];
    return;
  }

  if (mode.value === "file") {
    await store.createRawScan({
      ...options,
      alias: scanAlias.value,
      relative_path: scanRelativePath.value.trim(),
      glob: scanGlob.value.trim() || "*",
      recursive: scanRecursive.value,
    });
    return;
  }

  const parsed = resolvePayloadForMode();
  if (!parsed.ok) return;
  await store.createImport(mode.value, parsed.value, options);
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>创建知识导入任务</h2>
        <span class="section-label">支持文本粘贴、多文件上传、工作区扫描与结构化直写</span>
      </div>
    </div>

    <div class="grid-3">
      <div class="field">
        <label for="import-mode-select">导入模式</label>
        <select id="import-mode-select" v-model="mode" class="select">
          <option value="text">文本粘贴（纯文本 / JSON 聊天记录）</option>
          <option value="upload">文件上传（支持批量拖拽）</option>
          <option value="file">工作区目录扫描（raw / plugin_data）</option>
          <option value="json">结构化 JSON 负载</option>
          <option value="paragraph">记忆段落直写</option>
          <option value="relation">知识关系边直写</option>
        </select>
      </div>

      <div v-if="supportsKnowledgeType" class="field">
        <label for="import-ktype-select">知识类型（knowledge_type）</label>
        <select id="import-ktype-select" v-model="knowledgeType" class="select">
          <option
            v-for="opt in KNOWLEDGE_TYPE_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </option>
        </select>
      </div>

      <div class="field">
        <label for="import-source-input">数据来源标识（source）</label>
        <input
          id="import-source-input"
          ref="sourceInput"
          v-model="source"
          class="input"
          placeholder="web_import"
          @focus="sourceMenu.open()"
          @input="sourceMenu.onInput()"
        />
      </div>
    </div>

    <!-- 模式 1：多文件拖拽上传区 -->
    <div v-if="mode === 'upload'" class="mode-block">
      <input
        ref="fileInputRef"
        type="file"
        multiple
        accept=".txt,.md,.json"
        class="hidden-file-input"
        @change="onSelectFiles"
      />
      <div
        class="dropzone"
        :class="{ dragging: isDragOver }"
        role="button"
        tabindex="0"
        aria-label="选择或拖拽 .txt / .md / .json 文件导入"
        @click="fileInputRef?.click()"
        @keydown.enter.prevent="fileInputRef?.click()"
        @keydown.space.prevent="fileInputRef?.click()"
        @dragover.prevent="isDragOver = true"
        @dragleave.prevent="isDragOver = false"
        @drop.prevent="onDropFiles"
      >
        <strong>将文件拖拽至此处，或点击选择多个文件</strong>
        <span>支持批量上传 .txt / .md / .json 文件，通过安全凭据通道逐个提交</span>
      </div>

      <div v-if="files.length" class="queued-files-section">
        <div class="queued-head">
          <span class="section-label tabular">已选待传文件（{{ files.length }} 个）</span>
          <button class="btn sm" type="button" :disabled="creating" @click="clearFiles">
            清空列表
          </button>
        </div>
        <div class="queued-list">
          <div v-for="(f, idx) in files" :key="`${f.name}-${f.size}-${idx}`" class="queued-row">
            <div class="queued-info">
              <span class="tag mono">{{ fileSuffixBadge(f.name) }}</span>
              <span class="queued-name">{{ f.name }}</span>
              <span class="tag mono tabular">{{ formatBytes(f.size) }}</span>
            </div>
            <button
              class="btn sm"
              type="button"
              :disabled="creating"
              @click="removeFile(idx)"
            >
              移除文件
            </button>
          </div>
        </div>
      </div>

      <p v-if="uploadProgressText" class="upload-progress mono tabular">{{ uploadProgressText }}</p>
    </div>

    <!-- 模式 2：工作区目录扫描模式（create_raw_scan） -->
    <div v-else-if="mode === 'file'" class="mode-block flat-section">
      <p class="scan-notice">
        扫描服务端受控的工作区别名目录（<code class="mono">raw</code> 或
        <code class="mono">plugin_data</code>）下的 <code class="mono">.txt / .md / .json</code> 文件。
      </p>
      <div class="grid-3">
        <div class="field">
          <label for="scan-alias-select">路径别名（alias）</label>
          <select id="scan-alias-select" v-model="scanAlias" class="select">
            <option value="raw">raw（原始语料目录）</option>
            <option value="plugin_data">plugin_data（插件数据根目录）</option>
          </select>
        </div>
        <div class="field">
          <label for="scan-rel-input">相对子目录（可选）</label>
          <input
            id="scan-rel-input"
            v-model="scanRelativePath"
            class="input mono"
            placeholder="例如：novels/vol1"
          />
        </div>
        <div class="field">
          <label for="scan-glob-input">通配符规则（glob）</label>
          <input
            id="scan-glob-input"
            v-model="scanGlob"
            class="input mono"
            placeholder="* 或 *.md"
          />
        </div>
      </div>
      <div class="scan-foot">
        <label class="checkbox-line">
          <input v-model="scanRecursive" type="checkbox" />
          <span>递归扫描子目录（recursive）</span>
        </label>
        <span v-if="activeAliasPathHint" class="section-label mono">
          基准路径：{{ activeAliasPathHint }}
        </span>
      </div>
    </div>

    <!-- 模式 3：文本 / JSON / 段落 / 关系输入 -->
    <div v-else class="mode-block field">
      <label for="import-payload-textarea">
        {{
          mode === "text"
            ? "待导入文本内容（支持纯文本、Markdown 或 JSON 聊天记录原文）"
            : mode === "json"
              ? "结构化 JSON 数据负载"
              : mode === "paragraph"
                ? "记忆段落文本或段落数组 JSON"
                : "知识三元组关系 JSON"
        }}
      </label>
      <textarea
        id="import-payload-textarea"
        v-model="payload"
        class="textarea"
        rows="6"
        :placeholder="
          mode === 'text'
            ? '粘贴长文档、设定集或 JSON 格式聊天记录（保持原始字符串提交）…'
            : '输入合法 JSON 对象或数组…'
        "
      ></textarea>
    </div>

    <!-- 聊天记录滑窗配置 -->
    <div v-if="supportsChatLog" class="flat-section">
      <label class="checkbox-line">
        <input v-model="chatLog" type="checkbox" />
        <span>按聊天记录导入（启用对话滑动窗口切分与上下文重叠）</span>
      </label>
      <div v-if="chatLog" class="grid-2" style="margin-top: 10px">
        <div class="field">
          <label for="chat-win-input">窗口字符数（narrative_window_size）</label>
          <input
            id="chat-win-input"
            v-model.number="windowSize"
            type="number"
            min="200"
            max="100000"
            class="input tabular"
          />
        </div>
        <div class="field">
          <label for="chat-overlap-input">重叠字符数（narrative_overlap）</label>
          <input
            id="chat-overlap-input"
            v-model.number="overlap"
            type="number"
            min="0"
            :max="Math.max(0, windowSize - 1)"
            class="input tabular"
          />
        </div>
      </div>
    </div>

    <details class="advanced-panel">
      <summary>展开高级选项覆盖（Options JSON，显式键值优先）</summary>
      <div class="field" style="margin-top: 10px">
        <label for="import-options-json">Options JSON</label>
        <textarea
          id="import-options-json"
          v-model="optionsText"
          class="textarea mono"
          rows="3"
          placeholder='{"batch_size": 8}'
        ></textarea>
      </div>
    </details>

    <div class="toolbar" style="margin-top: 14px">
      <button class="btn primary" type="button" :disabled="!canSubmit" @click="onCreate">
        {{
          creating
            ? "提交中…"
            : mode === "upload"
              ? `上传并导入（${files.length} 个文件）`
              : mode === "file"
                ? "扫描目录并导入"
                : "创建导入任务"
        }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.mode-block {
  margin-top: 12px;
}

.hidden-file-input {
  display: none;
}

.flat-section {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.queued-files-section {
  margin-top: 10px;
}

.queued-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.queued-list {
  display: flex;
  flex-direction: column;
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--surface-soft);
}

.queued-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--hairline);
}

.queued-row:last-child {
  border-bottom: 0;
}

.queued-info {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.queued-name {
  font-size: 12.5px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-progress {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--accent);
}

.scan-notice {
  margin: 0 0 10px;
  font-size: 12.5px;
  color: var(--muted);
  line-height: 1.55;
}

.scan-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.checkbox-line {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
}

.checkbox-line input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
}
</style>
