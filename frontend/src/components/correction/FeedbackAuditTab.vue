<script setup lang="ts">
// Tab 2：对话反馈纠错审计（POST /v1/feedback）—— 主从审阅视图、纠错前后对比、动作时间线与一键回退。
import { computed, onMounted } from "vue";
import { useGovernanceStore } from "@/stores/governance";

const store = useGovernanceStore();

onMounted(() => {
  void store.loadFeedbackList();
});

const detail = computed(() => store.selectedFeedback);

const beforeItems = computed<string[]>(() => {
  const d = detail.value;
  if (!d) return [];
  const lines: string[] = [];
  const forgotten = d.rollback_plan_summary?.forgotten_relations ?? [];
  for (const r of forgotten) {
    const subj = r.subject || r.source || r.head || "";
    const pred = r.predicate || r.relation || r.label || "";
    const obj = r.object || r.target || r.tail || "";
    const desc = [subj, pred, obj].filter(Boolean).join(" → ") || JSON.stringify(r);
    lines.push(`遗忘旧关系：${desc}`);
  }
  const stale = d.rollback_plan_summary?.stale_marks ?? [];
  for (const s of stale) {
    lines.push(`标记过期证据：${s.paragraph_id || s.target_id || JSON.stringify(s)}`);
  }
  if (!lines.length && d.query_text) {
    lines.push(`触发查询上下文：${d.query_text}`);
  }
  return lines;
});

const afterItems = computed<string[]>(() => {
  const d = detail.value;
  if (!d) return [];
  const lines: string[] = [];
  const cw = d.rollback_plan_summary?.corrected_write;
  if (Array.isArray(cw)) {
    for (const item of cw) {
      lines.push(`写入新事实：${item.content || item.text || JSON.stringify(item)}`);
    }
  } else if (cw && typeof cw === "object") {
    lines.push(
      `写入修正内容：${(cw as Record<string, unknown>).content || (cw as Record<string, unknown>).text || JSON.stringify(cw)}`,
    );
  }
  const people = d.rollback_plan_summary?.profile_person_ids ?? [];
  if (people.length) {
    lines.push(`刷新人物画像：${people.join("、")}`);
  }
  const eps = d.rollback_plan_summary?.episode_sources ?? [];
  if (eps.length) {
    lines.push(`重算关联事件：${eps.join("、")}`);
  }
  if (!lines.length && d.decision) {
    lines.push(`判定结论：${d.decision}`);
  }
  return lines;
});

const canRollback = computed(() => {
  const d = detail.value;
  if (!d) return false;
  return d.rollback_status !== "rolled_back" && d.rollback_status !== "rolling_back";
});

function formatJson(obj: unknown): string {
  if (!obj) return "{}";
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

function formatTime(ts?: number | string): string {
  if (!ts) return "-";
  const num = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(String(ts));
  if (Number.isNaN(num)) return String(ts);
  return new Date(num).toLocaleString("zh-CN", { hour12: false });
}
</script>

<template>
  <div class="feedback-master-detail">
    <!-- 左栏：反馈任务筛选与主列表 -->
    <div class="band feedback-list-pane">
      <div class="panel-title">
        <div>
          <h2 class="tabular-nums">对话反馈审计（{{ store.feedbackItems.length }}）</h2>
          <span class="section-label">审计对话中触发的自动记忆纠错记录</span>
        </div>
        <button
          class="btn sm"
          type="button"
          :disabled="store.feedbackActionBusy"
          @click="store.reconcileFeedbackTasks()"
        >
          对账任务状态
        </button>
      </div>

      <div class="filter-stack">
        <div class="grid-2">
          <select
            v-model="store.feedbackFilters.status"
            class="select"
            aria-label="按任务状态筛选"
            @change="store.loadFeedbackList()"
          >
            <option value="">全部任务状态</option>
            <option value="completed">已完成（completed）</option>
            <option value="applied">已生效（applied）</option>
            <option value="ignored">未改写（ignored）</option>
            <option value="failed">异常（failed）</option>
          </select>
          <select
            v-model="store.feedbackFilters.rollbackStatus"
            class="select"
            aria-label="按回退状态筛选"
            @change="store.loadFeedbackList()"
          >
            <option value="">全部回退状态</option>
            <option value="none">未回退（none）</option>
            <option value="rolled_back">已回退（rolled_back）</option>
            <option value="failed">回退失败（failed）</option>
          </select>
        </div>

        <div class="query-search-row">
          <input
            v-model="store.feedbackFilters.query"
            class="input"
            type="search"
            placeholder="搜索原查询语句或会话 ID…"
            aria-label="搜索反馈查询或会话"
            @keyup.enter="store.loadFeedbackList()"
          />
          <button
            class="btn primary"
            type="button"
            :disabled="store.feedbackLoading"
            @click="store.loadFeedbackList()"
          >
            检索记录
          </button>
        </div>
      </div>

      <div v-if="store.feedbackLoading && !store.feedbackItems.length" class="empty-state">
        正在加载反馈记录…
      </div>
      <div v-else-if="store.feedbackItems.length" class="task-items-scroll">
        <button
          v-for="item in store.feedbackItems"
          :key="item.task_id"
          type="button"
          class="feedback-item-row"
          :class="{ active: detail?.task_id === item.task_id }"
          @click="store.selectFeedbackTask(item.task_id)"
        >
          <div class="item-top">
            <strong class="mono tabular-nums">#{{ item.task_id }}</strong>
            <div class="tags" style="margin-top: 0">
              <span class="tag info">{{ item.decision || item.task_status || "recorded" }}</span>
              <span
                v-if="item.rollback_status && item.rollback_status !== 'none'"
                class="tag"
                :class="item.rollback_status === 'rolled_back' ? 'teal' : 'bad'"
              >
                {{ item.rollback_status }}
              </span>
            </div>
          </div>
          <p class="item-query">{{ item.query_text || "（无查询文本快照）" }}</p>
          <span class="section-label tabular-nums">
            {{ item.session_id || "未知会话" }} · {{ formatTime(item.created_at || item.query_timestamp) }}
          </span>
        </button>
      </div>
      <div v-else class="empty-state">
        <strong>暂无匹配的反馈纠错记录</strong>
        <p>用户在聊天中指出记忆错误并触发自动纠错后，审计记录将在此归档。</p>
      </div>
    </div>

    <!-- 右栏：纠错前后对比 + 动作日志时间线 + JSON 快照 -->
    <div class="band feedback-detail-pane">
      <div v-if="store.feedbackDetailLoading && !detail" class="empty-state">
        正在读取审计详情…
      </div>

      <template v-else-if="detail">
        <div class="panel-title">
          <div>
            <h2 class="tabular-nums">审计详情 #{{ detail.task_id }}</h2>
            <span class="section-label tabular-nums">
              会话：{{ detail.session_id || "-" }} · 置信度
              {{
                typeof detail.decision_confidence === "number"
                  ? `${(detail.decision_confidence * 100).toFixed(1)}%`
                  : "-"
              }}
            </span>
          </div>
          <button
            v-if="canRollback"
            class="btn danger"
            type="button"
            :disabled="store.feedbackActionBusy"
            @click="store.rollbackFeedbackTask(detail.task_id)"
          >
            {{ store.feedbackActionBusy ? "正在回退…" : "回退该纠错" }}
          </button>
          <span v-else class="tag teal">已回退</span>
        </div>

        <!-- 纠错前 (Amber) -> 纠错后 (Green) 对比（第 2 层容器） -->
        <div class="diff-grid">
          <div class="diff-card before">
            <h4>纠错前 · 淘汰或标记过期的旧记忆</h4>
            <ul v-if="beforeItems.length" class="diff-list">
              <li v-for="(line, i) in beforeItems" :key="i">{{ line }}</li>
            </ul>
            <p v-else class="section-label">未涉及旧关系删除或段落过期标记</p>
          </div>

          <div class="diff-card after">
            <h4>纠错后 · 新写入事实与画像同步</h4>
            <ul v-if="afterItems.length" class="diff-list">
              <li v-for="(line, i) in afterItems" :key="i">{{ line }}</li>
            </ul>
            <p v-else class="section-label">未产生新增改写内容</p>
          </div>
        </div>

        <!-- 动作日志时间线（扁平细线行） -->
        <div class="timeline-block">
          <h3 class="tabular-nums">动作审计时间线（{{ store.selectedFeedbackLogs.length }}）</h3>
          <div v-if="store.selectedFeedbackLogs.length" class="timeline-list">
            <div
              v-for="(log, idx) in store.selectedFeedbackLogs"
              :key="log.id ?? idx"
              class="timeline-row"
            >
              <div class="timeline-head">
                <span class="tag info">{{ log.action_type || log.action || "step" }}</span>
                <span
                  class="tag"
                  :class="log.status === 'failed' ? 'bad' : 'ok'"
                >
                  {{ log.status || "ok" }}
                </span>
                <span v-if="log.target_type || log.target_id" class="mono section-label">
                  {{ log.target_type }}:{{ log.target_id }}
                </span>
                <span class="section-label tabular-nums">{{ formatTime(log.created_at) }}</span>
              </div>
              <p v-if="log.message || log.detail" class="timeline-detail">
                {{ typeof log.detail === "string" ? log.detail : log.message || formatJson(log.detail) }}
              </p>
            </div>
          </div>
          <div v-else class="empty-state">暂无动作明细日志</div>
        </div>

        <details class="advanced-panel">
          <summary>展开检索快照与决策载荷 JSON</summary>
          <div class="grid-2" style="margin-top: 10px">
            <div>
              <span class="section-label">决策载荷（decision_payload）</span>
              <pre class="json mono">{{ formatJson(detail.decision_payload) }}</pre>
            </div>
            <div>
              <span class="section-label">回滚摘要（rollback_plan_summary）</span>
              <pre class="json mono">{{ formatJson(detail.rollback_plan_summary) }}</pre>
            </div>
          </div>
        </details>
      </template>

      <div v-else class="empty-state">
        <strong>请从左侧选择一条反馈审计记录</strong>
        <p>可核查纠错前后的事实变更差异、动作时间线，或对误判纠错执行回退。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tabular-nums {
  font-variant-numeric: tabular-nums;
}

.feedback-master-detail {
  display: grid;
  grid-template-columns: minmax(280px, 0.84fr) minmax(0, 1.16fr);
  gap: 16px;
  align-items: start;
}

.feedback-list-pane,
.feedback-detail-pane {
  margin-top: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.filter-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.query-search-row {
  display: flex;
  gap: 8px;
}

.query-search-row .input {
  flex: 1;
  min-width: 0;
}

.task-items-scroll {
  display: flex;
  flex-direction: column;
  max-height: 540px;
  overflow-y: auto;
  border-top: 1px solid var(--hairline);
}

.feedback-item-row {
  width: 100%;
  text-align: left;
  padding: 11px 10px;
  border: 0;
  border-bottom: 1px solid var(--hairline);
  border-left: 2px solid transparent;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.feedback-item-row:hover {
  background: var(--nav-soft);
}

.feedback-item-row.active {
  border-left-color: var(--accent);
  background: var(--accent-soft);
}

.item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.item-query {
  margin: 6px 0 4px;
  font-size: 13px;
  color: var(--ink);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.diff-list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12.5px;
  color: var(--text);
}

.timeline-block {
  padding-top: 6px;
  border-top: 1px solid var(--hairline);
}

.timeline-block h3 {
  margin: 4px 0 8px;
  font-size: 14px;
  font-weight: 680;
  color: var(--ink);
}

.timeline-list {
  display: flex;
  flex-direction: column;
  max-height: 260px;
  overflow-y: auto;
  border-top: 1px solid var(--hairline);
}

.timeline-row {
  padding: 9px 4px;
  border-bottom: 1px solid var(--hairline);
}

.timeline-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
}

.timeline-detail {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--muted);
  overflow-wrap: anywhere;
}

@media (max-width: 960px) {
  .feedback-master-detail {
    grid-template-columns: 1fr;
  }
}
</style>
