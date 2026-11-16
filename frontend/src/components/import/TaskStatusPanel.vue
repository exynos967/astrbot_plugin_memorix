<script setup lang="ts">
// 导入任务监控与后台管理面板：
// 1. 扁平化层级（消除 .band > .result > .file-card > .chunk-row 四层盒套盒），采用细线分割行。
// 2. 支持取消任务（cancel）、失败分块一键重试（retry_failed）及文件分块明细检查器（get_chunks）。
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useTaskStore } from "@/stores/task";
import { useAppStore } from "@/stores/app";
import { isTerminalStatus, type ImportFileItem, type TaskDetail } from "@/services/taskApi";
import { taskStatusLabel, taskProgressPct } from "@/utils/episodeText";

const store = useTaskStore();
const app = useAppStore();
const {
  currentTaskId,
  taskDetail,
  importTasks,
  chunksByFile,
  polling,
  loadingTasks,
  actionBusy,
} = storeToRefs(store);

const expandedFileIds = ref<Record<string, boolean>>({});

watch(currentTaskId, () => {
  expandedFileIds.value = {};
});

onBeforeUnmount(() => {
  store.stopPolling();
});

const detailJson = computed(() =>
  taskDetail.value ? JSON.stringify(taskDetail.value, null, 2) : "",
);

const statusLabel = computed(() =>
  taskDetail.value ? taskStatusLabel(taskDetail.value.status) : "",
);

const progressLabel = computed(() => taskProgressPct(taskDetail.value));

const progressNumber = computed(() => {
  const t = taskDetail.value;
  if (!t) return 0;
  if (typeof t.progress === "number") {
    return Math.max(0, Math.min(100, Math.round(t.progress * 100)));
  }
  if (t.total_chunks && t.total_chunks > 0) {
    return Math.max(0, Math.min(100, Math.round(((t.done_chunks || 0) / t.total_chunks) * 100)));
  }
  return isTerminalStatus(t.status) && (t.status === "succeeded" || t.status === "completed")
    ? 100
    : 0;
});

const errorBannerText = computed(() => {
  const t = taskDetail.value;
  if (!t) return "";
  return String(t.error_message || t.error || "").trim();
});

const canCancel = computed(() => {
  const t = taskDetail.value;
  if (!t || !t.task_id) return false;
  return !isTerminalStatus(t.status) && !t.cancel_requested;
});

const canRetryFailed = computed(() => {
  const t = taskDetail.value;
  if (!t || !t.task_id) return false;
  return isTerminalStatus(t.status) && (t.failed_chunks || 0) > 0;
});

function sourceKindLabel(src: string | undefined): string {
  const map: Record<string, string> = {
    upload: "文件上传",
    paste: "文本粘贴",
    raw_scan: "目录扫描",
  };
  return (src && map[src]) || src || "导入";
}

function statusTagClass(status: string | undefined): string {
  if (!status) return "";
  if (status === "succeeded" || status === "completed") return "ok";
  if (status === "failed") return "bad";
  if (status === "completed_with_errors" || status === "canceled" || status === "cancelled") {
    return "warn";
  }
  return "";
}

async function onConfirmCancel(): Promise<void> {
  const id = currentTaskId.value.trim();
  if (!id) return;
  const ok = await app.requestConfirmation({
    title: "取消后台导入任务",
    message: `确定中止正在排队或运行中的导入任务「${id.slice(0, 12)}」吗？已完成的分块会保留，未执行分块标记为已取消。`,
    confirmText: "取消任务",
    cancelText: "继续运行",
    danger: true,
  });
  if (!ok) return;
  await store.cancelTask(id);
}

function toggleFileChunks(task: TaskDetail, file: ImportFileItem): void {
  const fid = file.file_id;
  if (!fid) return;
  const key = `${task.task_id}::${fid}`;
  const nextOpen = !expandedFileIds.value[key];
  expandedFileIds.value = { ...expandedFileIds.value, [key]: nextOpen };
  if (nextOpen && !chunksByFile.value[key]?.items?.length) {
    void store.loadFileChunks(task.task_id, fid);
  }
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>导入任务监控与分块管理</h2>
        <span class="section-label">查看实时分块进度、重试失败分块或中止队列任务</span>
      </div>
      <div class="tags" style="margin-top: 0">
        <span v-if="polling" class="tag info">自动轮询中</span>
        <button
          v-if="polling"
          class="btn sm"
          type="button"
          @click="store.stopPolling()"
        >
          暂停轮询
        </button>
        <button
          class="btn sm"
          type="button"
          :disabled="loadingTasks"
          @click="store.loadImportTasks()"
        >
          {{ loadingTasks ? "同步中…" : "同步任务列表" }}
        </button>
      </div>
    </div>

    <!-- 单一任务查询栏 -->
    <div class="toolbar task-lookup-bar">
      <div class="field" style="flex: 1">
        <label for="import-task-id-input">查询导入 Task ID</label>
        <input
          id="import-task-id-input"
          v-model="currentTaskId"
          class="input mono"
          placeholder="输入或从下方队列选择导入 task_id"
          @keydown.enter.prevent="store.selectTask(currentTaskId)"
        />
      </div>
      <button
        class="btn"
        type="button"
        :disabled="!currentTaskId.trim()"
        @click="store.refresh()"
      >
        查询任务状态
      </button>
    </div>

    <!-- 近期导入任务队列（细线行列表） -->
    <div v-if="importTasks.length" class="recent-tasks">
      <div class="section-label tabular" style="margin-bottom: 6px">
        近期导入队列（{{ importTasks.length }}）
      </div>
      <div class="recent-task-list">
        <button
          v-for="item in importTasks.slice(0, 8)"
          :key="item.task_id"
          type="button"
          class="recent-task-row"
          :class="{ active: item.task_id === currentTaskId }"
          @click="store.selectTask(item.task_id)"
        >
          <div class="recent-task-main">
            <span class="mono task-short-id">{{ item.task_id.slice(0, 10) }}</span>
            <span class="tag">{{ sourceKindLabel(item.source) }}</span>
            <span class="tag" :class="statusTagClass(item.status)">
              {{ taskStatusLabel(item.status) }}
            </span>
          </div>
          <div class="tags" style="margin-top: 0">
            <span v-if="item.total_chunks" class="tag mono tabular">
              {{ item.done_chunks || 0 }}/{{ item.total_chunks }} 块
            </span>
            <span v-if="item.failed_chunks" class="tag bad tabular">
              失败 {{ item.failed_chunks }}
            </span>
            <span class="tag mono tabular">{{ taskProgressPct(item) }}</span>
          </div>
        </button>
      </div>
    </div>

    <!-- 当前任务结构化详情（扁平分区） -->
    <div v-if="!taskDetail" class="empty" style="margin-top: 12px">
      暂无选中的导入任务，请在上方创建任务或点击近期队列查看进度
    </div>
    <div v-else class="task-detail-section">
      <div class="task-detail-head">
        <div class="tags" style="margin-top: 0">
          <h3>{{ statusLabel }}</h3>
          <span class="tag mono">{{ taskDetail.task_id.slice(0, 12) }}</span>
          <span v-if="taskDetail.current_step" class="tag mono">
            阶段：{{ taskDetail.current_step }}
          </span>
        </div>
        <div class="tags" style="margin-top: 0">
          <button
            v-if="canCancel"
            class="btn sm danger"
            type="button"
            :disabled="actionBusy"
            @click="onConfirmCancel"
          >
            {{ actionBusy ? "取消中…" : "取消任务" }}
          </button>
          <button
            v-if="canRetryFailed"
            class="btn sm primary"
            type="button"
            :disabled="actionBusy"
            @click="store.retryFailed(taskDetail.task_id)"
          >
            {{ actionBusy ? "提交中…" : `重试失败分块（${taskDetail.failed_chunks}）` }}
          </button>
        </div>
      </div>

      <!-- 进度条 -->
      <div class="progress-wrap">
        <div class="progress-track">
          <div
            class="progress-fill"
            :class="{ 'is-failed': taskDetail.status === 'failed' }"
            :style="{ width: `${progressNumber}%` }"
          ></div>
        </div>
        <span class="progress-pct mono tabular">{{ progressLabel }}</span>
      </div>

      <!-- 分块与文件统计 -->
      <div class="tags stats-strip">
        <span v-if="taskDetail.file_count != null || taskDetail.files?.length" class="tag tabular">
          文件 {{ taskDetail.file_count ?? taskDetail.files?.length ?? 0 }}
        </span>
        <span v-if="taskDetail.total_chunks != null" class="tag tabular">
          总分块 {{ taskDetail.total_chunks }}
        </span>
        <span v-if="taskDetail.done_chunks != null" class="tag ok tabular">
          成功 {{ taskDetail.done_chunks }}
        </span>
        <span v-if="taskDetail.failed_chunks" class="tag bad tabular">
          失败 {{ taskDetail.failed_chunks }}
        </span>
        <span v-if="taskDetail.cancelled_chunks" class="tag warn tabular">
          已取消 {{ taskDetail.cancelled_chunks }}
        </span>
      </div>

      <div v-if="errorBannerText" class="error-banner">
        {{ errorBannerText }}
      </div>

      <!-- 文件级进度与分块检查器（细线展开列表，无 4 层嵌套卡片） -->
      <div v-if="taskDetail.files?.length" class="files-section">
        <div class="section-label">文件与分块执行明细</div>
        <div class="rows file-list">
          <div
            v-for="(file, fIdx) in taskDetail.files"
            :key="file.file_id || fIdx"
            class="file-row"
          >
            <div class="file-row-head">
              <div class="tags" style="margin-top: 0">
                <strong>{{ file.name || `文件 #${fIdx + 1}` }}</strong>
                <span class="tag" :class="statusTagClass(file.status)">
                  {{ taskStatusLabel(file.status) }}
                </span>
                <span v-if="file.total_chunks != null" class="tag mono tabular">
                  {{ file.done_chunks || 0 }}/{{ file.total_chunks }} 块
                </span>
                <span v-if="file.failed_chunks" class="tag bad tabular">
                  失败 {{ file.failed_chunks }}
                </span>
              </div>
              <button
                v-if="file.file_id"
                class="btn sm"
                type="button"
                @click="toggleFileChunks(taskDetail, file)"
              >
                {{
                  expandedFileIds[`${taskDetail.task_id}::${file.file_id}`]
                    ? "收起分块"
                    : "查看分块"
                }}
              </button>
            </div>

            <div v-if="file.error" class="error-banner sm-banner">{{ file.error }}</div>

            <div
              v-if="file.file_id && expandedFileIds[`${taskDetail.task_id}::${file.file_id}`]"
              class="chunk-list"
            >
              <div
                v-if="chunksByFile[`${taskDetail.task_id}::${file.file_id}`]?.loading"
                class="empty"
              >
                正在加载分块状态…
              </div>
              <div
                v-else-if="!chunksByFile[`${taskDetail.task_id}::${file.file_id}`]?.items?.length"
                class="empty"
              >
                暂无分块明细
              </div>
              <div
                v-for="chunk in chunksByFile[`${taskDetail.task_id}::${file.file_id}`]?.items || []"
                :key="chunk.chunk_id || chunk.index"
                class="chunk-row"
              >
                <div class="tags" style="margin-top: 0">
                  <span class="tag mono tabular">#{{ chunk.index ?? 0 }}</span>
                  <span v-if="chunk.chunk_type" class="tag">{{ chunk.chunk_type }}</span>
                  <span class="tag" :class="statusTagClass(chunk.status)">
                    {{ taskStatusLabel(chunk.status) }}
                  </span>
                  <span v-if="chunk.step" class="tag mono">{{ chunk.step }}</span>
                </div>
                <p v-if="chunk.content_preview" class="chunk-preview">
                  {{ chunk.content_preview }}
                </p>
                <p v-if="chunk.error" class="chunk-error">{{ chunk.error }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <details class="advanced-panel">
        <summary>展开任务原始响应 JSON</summary>
        <pre class="json" style="margin-top: 8px">{{ detailJson }}</pre>
      </details>
    </div>
  </div>
</template>

<style scoped>
.task-lookup-bar {
  align-items: flex-end;
}

.recent-tasks {
  margin-top: 12px;
}

.recent-task-list {
  display: flex;
  flex-direction: column;
  max-height: 220px;
  overflow-y: auto;
  border-top: 1px solid var(--hairline);
}

.recent-task-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
  padding: 9px 8px 9px 10px;
  text-align: left;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--hairline);
  border-left: 2px solid transparent;
  color: var(--text);
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.recent-task-row:hover {
  background: var(--nav-soft);
}

.recent-task-row.active {
  border-left-color: var(--accent);
  background: var(--accent-soft);
}

.recent-task-main {
  display: flex;
  align-items: center;
  gap: 6px;
}

.task-short-id {
  font-size: 12px;
}

.task-detail-section {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.task-detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.task-detail-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 680;
  color: var(--ink);
}

.progress-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}

.progress-fill.is-failed {
  background: var(--red);
}

.progress-pct {
  font-size: 12px;
  color: var(--muted);
  min-width: 46px;
  text-align: right;
}

.stats-strip {
  margin-top: 0;
  gap: 6px;
}

.error-banner {
  padding: 8px 10px;
  color: var(--red);
  background: var(--red-soft);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
}

.sm-banner {
  margin-top: 6px;
  padding: 6px 8px;
}

.files-section {
  padding-top: 8px;
  border-top: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-list {
  border-top: 1px solid var(--hairline);
}

.file-row {
  padding: 9px 2px;
}

.file-row-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}

.chunk-list {
  margin-top: 8px;
  padding-left: 12px;
  border-left: 2px solid var(--hairline);
  display: flex;
  flex-direction: column;
  max-height: 260px;
  overflow-y: auto;
}

.chunk-row {
  padding: 7px 0;
  border-bottom: 1px solid var(--hairline);
  font-size: 12px;
}

.chunk-row:last-child {
  border-bottom: 0;
}

.chunk-preview {
  margin: 4px 0 0;
  color: var(--muted);
  white-space: pre-wrap;
  word-break: break-word;
}

.chunk-error {
  margin: 4px 0 0;
  color: var(--red);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
