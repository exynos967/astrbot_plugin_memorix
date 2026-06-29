<script setup lang="ts">
// 任务状态展示面板。
// 从 legacy view-import 左列任务状态（index.html 行 2260-2266, 4431-4437）迁移。
// Task ID 输入框直接 v-model store.currentTaskId（用户可编辑后点刷新）；
// 任务详情用 taskStatusLabel + taskProgressPct + current_step + error_message + 结果 JSON 展示。
// 新增轮询状态指示（legacy 无轮询），polling 时显示"轮询中…"并可手动停止。
import { computed } from "vue";
import { storeToRefs } from "pinia";
import { useTaskStore } from "@/stores/task";
import { taskStatusLabel, taskProgressPct } from "@/utils/episodeText";

const store = useTaskStore();
const { currentTaskId, taskDetail, polling } = storeToRefs(store);

/** 任务详情 JSON 格式化展示。 */
const detailJson = computed(() =>
  taskDetail.value ? JSON.stringify(taskDetail.value, null, 2) : "",
);

const statusLabel = computed(() =>
  taskDetail.value ? taskStatusLabel(taskDetail.value.status) : "",
);

const progressLabel = computed(() => taskProgressPct(taskDetail.value));
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <h2>任务状态</h2>
      <button class="btn" @click="store.refresh()">刷新任务</button>
    </div>
    <div class="toolbar">
      <div class="field">
        <label>Task ID</label>
        <input v-model="currentTaskId" class="input" placeholder="task_id" />
      </div>
      <span v-if="polling" class="polling-tag">轮询中…</span>
      <button v-if="polling" class="btn" @click="store.stopPolling()">停止轮询</button>
    </div>

    <div v-if="!taskDetail" class="empty" style="margin-top: 12px">暂无任务</div>
    <div v-else class="result" style="margin-top: 12px">
      <div class="result-head">
        <h3>{{ statusLabel }}</h3>
        <div class="tags">
          <span class="tag">{{ progressLabel }}</span>
          <span v-if="taskDetail.current_step" class="tag mono">{{ taskDetail.current_step }}</span>
        </div>
      </div>
      <p v-if="taskDetail.error_message" class="error-text">{{ taskDetail.error_message }}</p>
      <pre class="json">{{ detailJson }}</pre>
    </div>
  </div>
</template>

<style scoped>
.polling-tag {
  color: var(--accent-strong);
  font-size: 12px;
  font-weight: 720;
}

.error-text {
  margin: 6px 0;
  color: #c0392b;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
