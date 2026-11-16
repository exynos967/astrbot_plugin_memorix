<script setup lang="ts">
// 会话摘要任务面板：使用独立的 summaryTaskId / summaryResult / pollingSummary 状态，与导入任务彻底隔离。
import { computed, onBeforeUnmount, ref } from "vue";
import { storeToRefs } from "pinia";
import { useTaskStore } from "@/stores/task";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import { taskProgressPct, taskStatusLabel } from "@/utils/episodeText";
import type { CandidateItem } from "@/stores/candidate";

const store = useTaskStore();
const graph = useGraphStore();
const app = useAppStore();
const {
  creatingSummary,
  pollingSummary,
  summaryTaskId,
  summaryResult,
} = storeToRefs(store);

onBeforeUnmount(() => {
  store.stopSummaryPolling();
});

const sessionId = ref("");
const source = ref("web_summary");
const contextLength = ref(50);
const messagesText = ref("[]");

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

function parseMessages(): unknown[] | null {
  try {
    const parsed = JSON.parse(messagesText.value.trim() || "[]");
    if (!Array.isArray(parsed)) {
      app.pushError("Messages JSON 必须是消息数组（例如 [{\"role\":\"user\",\"content\":\"...\"}]）", "摘要任务");
      return null;
    }
    if (!parsed.length) {
      app.pushError("请至少提供一条会话消息再提交摘要任务", "摘要任务");
      return null;
    }
    return parsed;
  } catch (err) {
    app.pushError(
      `Messages JSON 解析失败：${err instanceof Error ? err.message : "请检查 JSON 格式"}`,
      "摘要任务",
    );
    return null;
  }
}

async function onCreate(): Promise<void> {
  const messages = parseMessages();
  if (!messages) return;
  await store.createSummary(sessionId.value, source.value, messages, contextLength.value);
}

const summaryJson = computed(() =>
  summaryResult.value ? JSON.stringify(summaryResult.value, null, 2) : "",
);

const summaryErrorText = computed(() => {
  const r = summaryResult.value;
  if (!r) return "";
  return String(r.error_message || r.error || "").trim();
});

const summaryStatusClass = computed(() => {
  const s = summaryResult.value?.status;
  if (s === "succeeded" || s === "completed") return "ok";
  if (s === "failed") return "bad";
  return "";
});

const extractedSummaryText = computed(() => {
  const res = summaryResult.value?.result;
  if (!res || typeof res !== "object") return "";
  return String(
    (res as Record<string, unknown>).summary ||
      (res as Record<string, unknown>).summary_text ||
      (res as Record<string, unknown>).topic ||
      "",
  ).trim();
});
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>会话摘要任务</h2>
        <span class="section-label">从会话消息数组提炼记忆摘要与知识关系</span>
      </div>
    </div>

    <div class="grid-3">
      <div class="field">
        <label for="summary-session-input">会话 ID（可选）</label>
        <input
          id="summary-session-input"
          v-model="sessionId"
          class="input"
          placeholder="例如：group_10001"
        />
      </div>
      <div class="field">
        <label for="summary-source-input">数据来源（source）</label>
        <input
          id="summary-source-input"
          ref="sourceInput"
          v-model="source"
          class="input"
          placeholder="web_summary"
          @focus="sourceMenu.open()"
          @input="sourceMenu.onInput()"
        />
      </div>
      <div class="field">
        <label for="summary-ctx-input">上下文条数</label>
        <input
          id="summary-ctx-input"
          v-model.number="contextLength"
          class="input tabular"
          type="number"
          min="1"
          max="500"
        />
      </div>
    </div>

    <div class="field" style="margin-top: 10px">
      <label for="summary-messages-json">会话消息数组（Messages JSON）</label>
      <textarea
        id="summary-messages-json"
        v-model="messagesText"
        class="textarea mono"
        rows="6"
        placeholder='[{"role":"user","sender":"Alice","content":"今天我们去喝手冲咖啡吧"}]'
      ></textarea>
    </div>

    <div class="toolbar" style="margin-top: 12px">
      <button
        class="btn primary"
        type="button"
        :disabled="creatingSummary"
        @click="onCreate"
      >
        {{ creatingSummary ? "提交中…" : "创建摘要任务" }}
      </button>
    </div>

    <!-- 摘要任务独立查询与状态展示（扁平分割区） -->
    <div class="summary-monitor">
      <div class="toolbar summary-lookup-bar">
        <div class="field" style="flex: 1">
          <label for="summary-task-id-input">摘要 Task ID</label>
          <input
            id="summary-task-id-input"
            v-model="summaryTaskId"
            class="input mono"
            placeholder="输入摘要任务 task_id"
            @keydown.enter.prevent="store.refreshSummary()"
          />
        </div>
        <button
          class="btn"
          type="button"
          :disabled="!summaryTaskId.trim()"
          @click="store.refreshSummary()"
        >
          查询摘要状态
        </button>
        <button
          v-if="pollingSummary"
          class="btn sm"
          type="button"
          @click="store.stopSummaryPolling()"
        >
          暂停轮询
        </button>
      </div>

      <div v-if="!summaryResult" class="empty" style="margin-top: 10px">
        等待创建或查询摘要任务
      </div>
      <div v-else class="summary-result-section">
        <div class="summary-result-head">
          <div class="tags" style="margin-top: 0">
            <h3>{{ taskStatusLabel(summaryResult.status) }}</h3>
            <span class="tag tabular" :class="summaryStatusClass">
              {{ taskProgressPct(summaryResult) }}
            </span>
            <span v-if="pollingSummary" class="tag info">自动轮询中</span>
          </div>
          <span v-if="summaryResult.task_id" class="tag mono">
            {{ summaryResult.task_id.slice(0, 12) }}
          </span>
        </div>

        <div v-if="summaryErrorText" class="error-banner">
          {{ summaryErrorText }}
        </div>

        <p v-if="extractedSummaryText" class="summary-text">
          {{ extractedSummaryText }}
        </p>

        <details class="advanced-panel">
          <summary>展开摘要原始响应 JSON</summary>
          <pre class="json" style="margin-top: 8px">{{ summaryJson }}</pre>
        </details>
      </div>
    </div>
  </div>
</template>

<style scoped>
.summary-monitor {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
}

.summary-lookup-bar {
  align-items: flex-end;
}

.summary-result-section {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.summary-result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-result-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 680;
  color: var(--ink);
}

.summary-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: var(--ink);
  line-height: 1.62;
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
</style>
