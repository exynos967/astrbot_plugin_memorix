<script setup lang="ts">
// 在线会话记忆迁移工作台（Schema 31 ChatMemoryMigration）：
// 左侧选择源/目标会话并执行快照预检与原子迁移，右侧审计迁移账本并支持一键无损恢复。
import { computed, onMounted } from "vue";
import { useChatMigrationStore } from "@/stores/chatMigration";

const store = useChatMigrationStore();

onMounted(() => {
  void store.refreshAll();
});

const METRIC_ROWS = [
  { key: "paragraphs", label: "长期记忆段落" },
  { key: "facts", label: "结构化事实（FactClaims）" },
  { key: "episodes", label: "事件摘要（Episodes）" },
  { key: "image_occurrences", label: "图片会话出现记录" },
  { key: "image_observations", label: "图片视觉认知记录" },
] as const;

const isSameEndpoint = computed(() => {
  const src = store.form.sourceChatId.trim();
  const dst = store.form.targetChatId.trim();
  return Boolean(src && dst && src === dst);
});

const canRunPreview = computed(() => {
  const src = store.form.sourceChatId.trim();
  const dst = store.form.targetChatId.trim();
  return !store.previewing && Boolean(src && dst) && src !== dst;
});

const isPreviewStale = computed(() => {
  const job = store.activeJob;
  if (!job) return false;
  return (
    store.form.sourceChatId.trim() !== job.source_chat_id ||
    store.form.targetChatId.trim() !== job.target_chat_id ||
    store.form.migrateTranscript !== job.migrate_transcript
  );
});

const comparisonRows = computed(() => {
  const job = store.activeJob;
  if (!job) return [];
  const rows: Array<{ key: string; label: string; source: number; target: number }> = METRIC_ROWS.map(
    (m) => ({
      key: m.key,
      label: m.label,
      source: Number(job.source_counts?.[m.key] ?? 0) || 0,
      target: Number(job.target_counts?.[m.key] ?? 0) || 0,
    }),
  );
  if (job.migrate_transcript) {
    rows.push({
      key: "transcript_messages",
      label: "原始聊天流水（Transcript）",
      source: Number(job.transcript_counts?.source?.messages ?? 0) || 0,
      target: Number(job.transcript_counts?.target?.messages ?? 0) || 0,
    });
  }
  return rows;
});

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    previewed: "待执行",
    completed: "已迁移",
    restored: "已回滚",
    failed: "失败",
  };
  return map[status] || status;
}

function statusTagClass(status: string): string {
  if (status === "completed") return "ok";
  if (status === "restored") return "teal";
  if (status === "failed") return "bad";
  return "warn";
}

function formatTs(ts?: number): string {
  if (!ts || !Number.isFinite(ts)) return "-";
  try {
    return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "-";
  }
}

function pickAsSource(chatId: string): void {
  store.form.sourceChatId = chatId;
  store.activeJob = null;
  store.confirmOverwrite = false;
}

function pickAsTarget(chatId: string): void {
  store.form.targetChatId = chatId;
  store.activeJob = null;
  store.confirmOverwrite = false;
}
</script>

<template>
  <div class="chat-mig-workbench">
    <!-- 左栏：源/目标会话选择 + 预检快照 + 原子迁移执行 -->
    <div class="band mig-panel">
      <div class="panel-title">
        <div>
          <h2>在线会话记忆迁移</h2>
          <span class="section-label">
            将旧群聊或私聊的记忆段落、事实、事件与图片关联原子迁移至新会话（免重算向量，支持按账本无损回退）
          </span>
        </div>
        <button
          type="button"
          class="btn sm"
          :disabled="store.chatsLoading || store.jobsLoading"
          @click="store.refreshAll()"
        >
          {{ store.chatsLoading ? "刷新中…" : "刷新会话" }}
        </button>
      </div>

      <!-- 1. 候选会话快速点选（扁平分割区） -->
      <div class="mig-section first-section">
        <div class="block-head">
          <h3 class="sub-heading">1. 选定已有会话</h3>
          <div class="inline-search">
            <input
              v-model="store.chatSearch"
              class="input input-sm"
              type="search"
              placeholder="筛选群号 / 用户 / chat_id…"
              aria-label="筛选已有会话"
              @keyup.enter="store.loadChats()"
            />
            <button type="button" class="btn sm" :disabled="store.chatsLoading" @click="store.loadChats()">
              筛选会话
            </button>
          </div>
        </div>

        <div v-if="store.chats.length === 0" class="empty-state">
          {{ store.chatsLoading ? "正在扫描作用域会话…" : "当前作用域暂无已记录会话，可在下方直接填写会话 ID。" }}
        </div>
        <div v-else class="chat-candidate-box">
          <div
            v-for="item in store.chats"
            :key="item.chat_id"
            class="chat-candidate-row"
            :class="{
              'is-source': store.form.sourceChatId === item.chat_id,
              'is-target': store.form.targetChatId === item.chat_id,
            }"
          >
            <div class="chat-meta">
              <div class="chat-name">
                <span class="tag info">{{ item.is_group ? "群聊" : "会话" }}</span>
                <strong>{{ item.display_name }}</strong>
                <span v-if="store.form.sourceChatId === item.chat_id" class="tag">源</span>
                <span v-if="store.form.targetChatId === item.chat_id" class="tag teal">目标</span>
              </div>
              <div class="chat-stats mono tabular">
                段落 {{ item.paragraph_count }} · 事实 {{ item.fact_claim_count }} · 事件 {{ item.episode_count }} · 图片
                {{ Math.max(item.image_occurrence_count ?? 0, item.image_observation_count ?? 0) }} · 流水 {{ item.transcript_message_count }}
              </div>
            </div>
            <div class="chat-actions">
              <button type="button" class="btn sm" @click="pickAsSource(item.chat_id)">设为源会话</button>
              <button type="button" class="btn sm" @click="pickAsTarget(item.chat_id)">设为目标会话</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 2. 迁移端点表单 -->
      <div class="mig-section">
        <h3 class="sub-heading">2. 配置源会话与目标会话</h3>
        <div class="endpoint-grid">
          <div class="field">
            <label for="mig-source-id">源会话 ID（Source Chat ID）</label>
            <input
              id="mig-source-id"
              v-model="store.form.sourceChatId"
              class="input mono"
              :aria-invalid="isSameEndpoint || undefined"
              placeholder="例如：group_10001"
            />
          </div>
          <div class="swap-wrap">
            <button type="button" class="btn sm" title="交换源会话与目标会话" @click="store.swapEndpoints()">
              交换端点
            </button>
          </div>
          <div class="field">
            <label for="mig-target-id">目标会话 ID（Target Chat ID）</label>
            <input
              id="mig-target-id"
              v-model="store.form.targetChatId"
              class="input mono"
              :aria-invalid="isSameEndpoint || undefined"
              placeholder="例如：group_20002"
            />
          </div>
        </div>

        <p v-if="isSameEndpoint" class="field-error" role="alert">
          源会话 ID 与目标会话 ID 不能相同，请指定两个不同的会话。
        </p>

        <div class="options-row">
          <label class="checkbox-label">
            <input v-model="store.form.migrateTranscript" type="checkbox" />
            <span>同步迁移原始聊天流水（transcript_messages，回滚时同步恢复）</span>
          </label>
        </div>

        <div class="field">
          <label for="mig-note-input">迁移备注（可选）</label>
          <input
            id="mig-note-input"
            v-model="store.form.note"
            class="input"
            placeholder="例如：群号变更 10001 -> 20002"
          />
        </div>

        <div class="action-bar">
          <button
            type="button"
            class="btn primary"
            :disabled="!canRunPreview"
            @click="store.runPreview()"
          >
            {{ store.previewing ? "正在生成快照…" : "生成预检快照" }}
          </button>
        </div>
      </div>

      <!-- 3. 预检结果与确认执行 -->
      <div v-if="store.activeJob" class="mig-section">
        <div class="block-head">
          <div>
            <h3 class="sub-heading">3. 预检快照对比（<span class="mono">{{ store.activeJob.job_id }}</span>）</h3>
            <span class="mono section-label">
              {{ store.activeJob.source_chat_id }} → {{ store.activeJob.target_chat_id }} · 指纹
              {{ store.activeJob.expected_fingerprint.slice(0, 12) }}…
            </span>
          </div>
          <span class="tag" :class="statusTagClass(store.activeJob.status)">
            {{ statusLabel(store.activeJob.status) }}
          </span>
        </div>

        <p v-if="isPreviewStale && store.activeJob.can_execute" class="stale-banner" role="status">
          上方源/目标会话或流水选项已改动，请重新点击「生成预检快照」后再执行迁移。
        </p>

        <div class="diff-table-wrap">
          <table class="diff-table tabular">
            <thead>
              <tr>
                <th>数据维度</th>
                <th class="num">源会话待迁出</th>
                <th class="num">目标会话现有（归档保护）</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in comparisonRows" :key="row.key">
                <td>{{ row.label }}</td>
                <td class="num mono">{{ row.source }}</td>
                <td class="num mono">{{ row.target }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="store.activeJob.target_has_existing_data && store.activeJob.can_execute" class="conflict-alert">
          <div class="alert-title">目标会话已存在历史数据</div>
          <p class="alert-desc">
            迁移不会删除目标会话原有记录，而是隔离归档至
            <code class="mono">migration_archive:{{ store.activeJob.job_id }}</code>
            账本（日常检索自动隐藏，回滚迁移时完整还原）。
          </p>
          <label class="checkbox-label confirm-check">
            <input v-model="store.confirmOverwrite" type="checkbox" />
            <span>确认归档目标会话现有数据并迁入源会话记忆</span>
          </label>
        </div>

        <div v-if="store.activeJob.error" class="error-banner">
          {{ store.activeJob.error }}
        </div>

        <div v-if="store.activeJob.can_execute" class="action-bar">
          <button
            type="button"
            class="btn primary"
            :disabled="
              store.executing ||
              isPreviewStale ||
              (store.activeJob.target_has_existing_data && !store.confirmOverwrite)
            "
            @click="store.executeActiveJob(false)"
          >
            {{ store.executing ? "正在原子迁移…" : "执行原子迁移" }}
          </button>
          <button
            v-if="store.activeJob.status === 'failed'"
            type="button"
            class="btn"
            :disabled="
              store.executing ||
              isPreviewStale ||
              (store.activeJob.target_has_existing_data && !store.confirmOverwrite)
            "
            @click="store.executeActiveJob(true)"
          >
            刷新指纹并重试
          </button>
        </div>
      </div>
    </div>

    <!-- 右栏：迁移账本任务历史与一键恢复 -->
    <div class="band mig-panel">
      <div class="panel-title">
        <div>
          <h2 class="tabular">迁移账本记录（{{ store.jobs.length }}）</h2>
          <span class="section-label">
            基于 <code class="mono">chat_scope_transfers</code> 账本追踪每次迁移，支持无损回退
          </span>
        </div>
        <button type="button" class="btn sm" :disabled="store.jobsLoading" @click="store.loadJobs()">
          {{ store.jobsLoading ? "加载中…" : "刷新账本" }}
        </button>
      </div>

      <div v-if="store.jobs.length === 0" class="empty-state">
        {{ store.jobsLoading ? "正在读取迁移账本…" : "暂无会话迁移记录" }}
      </div>

      <div v-else class="job-list">
        <article
          v-for="job in store.jobs"
          :key="job.job_id"
          class="job-row"
          :class="{ selected: store.activeJob?.job_id === job.job_id }"
        >
          <div class="job-top">
            <button type="button" class="job-id-btn mono" @click="store.selectJob(job)">
              {{ job.job_id }}
            </button>
            <span class="tag" :class="statusTagClass(job.status)">
              {{ statusLabel(job.status) }}
            </span>
          </div>

          <div class="job-route mono">{{ job.source_chat_id }} → {{ job.target_chat_id }}</div>

          <div v-if="job.note" class="job-note">{{ job.note }}</div>

          <div class="section-label tabular">
            更新于 {{ formatTs(job.updated_at) }}
            <template v-if="job.applied_result?.transferred !== undefined">
              · 变更 {{ job.applied_result.transferred }} 项
            </template>
            <template v-if="job.restored_result?.restored !== undefined">
              · 已回退 {{ job.restored_result.restored }} 项
            </template>
          </div>

          <div v-if="job.error" class="error-inline">{{ job.error }}</div>

          <div class="job-actions">
            <button type="button" class="btn sm" @click="store.selectJob(job)">查看快照</button>
            <button
              v-if="job.can_recover"
              type="button"
              class="btn danger sm"
              :disabled="store.recoveringId === job.job_id"
              @click="store.recoverJob(job)"
            >
              {{ store.recoveringId === job.job_id ? "回滚中…" : "回滚迁移" }}
            </button>
          </div>
        </article>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-mig-workbench {
  display: grid;
  grid-template-columns: minmax(0, 1.14fr) minmax(0, 0.86fr);
  gap: 16px;
  align-items: start;
}

.mig-panel {
  margin-top: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.mig-section {
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mig-section.first-section {
  padding-top: 0;
  border-top: 0;
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.sub-heading {
  margin: 0;
  font-size: 13.5px;
  font-weight: 680;
  color: var(--ink);
}

.inline-search {
  display: flex;
  align-items: center;
  gap: 6px;
}

.input-sm {
  min-height: 32px;
  padding: 4px 10px;
  font-size: 12.5px;
}

.chat-candidate-box {
  max-height: 220px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--surface-soft);
}

.chat-candidate-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--hairline);
  border-left: 2px solid transparent;
}

.chat-candidate-row:last-child {
  border-bottom: 0;
}

.chat-candidate-row.is-source {
  border-left-color: var(--accent);
  background: var(--accent-soft);
}

.chat-candidate-row.is-target {
  border-left-color: var(--teal);
  background: var(--teal-soft);
}

.chat-meta {
  min-width: 0;
  flex: 1 1 220px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.chat-name {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13px;
  color: var(--ink);
}

.chat-stats {
  font-size: 11.5px;
  color: var(--muted);
}

.chat-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex-shrink: 0;
}

.endpoint-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 10px;
  align-items: end;
}

.swap-wrap {
  display: flex;
  align-items: center;
  padding-bottom: 2px;
}

.field-error {
  margin: 0;
  font-size: 12.5px;
  color: var(--red);
}

.stale-banner {
  margin: 0;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  background: var(--amber-soft);
  color: var(--amber);
  font-size: 12.5px;
}

.options-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.checkbox-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
}

.checkbox-label input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
}

.action-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 2px;
}

.diff-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.diff-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.diff-table th,
.diff-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--hairline);
  text-align: left;
}

.diff-table tr:last-child td {
  border-bottom: 0;
}

.diff-table th {
  color: var(--muted);
  background: var(--surface-soft);
  font-weight: 650;
  font-size: 12px;
}

.diff-table .num {
  text-align: right;
}

.conflict-alert {
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--amber-soft);
  border: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.alert-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--amber);
}

.alert-desc {
  margin: 0;
  font-size: 12.5px;
  color: var(--text);
  line-height: 1.5;
}

.confirm-check {
  font-weight: 600;
}

.error-banner,
.error-inline {
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--red-soft);
  color: var(--red);
  font-size: 12.5px;
}

.job-list {
  display: flex;
  flex-direction: column;
  max-height: 620px;
  overflow-y: auto;
  border-top: 1px solid var(--hairline);
}

.job-row {
  padding: 11px 10px;
  border-bottom: 1px solid var(--hairline);
  border-left: 2px solid transparent;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.job-row.selected {
  border-left-color: var(--accent);
  background: var(--accent-soft);
}

.job-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.job-id-btn {
  padding: 0;
  border: 0;
  background: transparent;
  font-size: 12.5px;
  font-weight: 680;
  color: var(--accent);
  cursor: pointer;
}

.job-id-btn:hover {
  text-decoration: underline;
}

.job-route {
  font-size: 13px;
  color: var(--ink);
  word-break: break-all;
}

.job-note {
  font-size: 12.5px;
  color: var(--text);
}

.job-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 2px;
}

@media (max-width: 980px) {
  .chat-mig-workbench {
    grid-template-columns: 1fr;
  }

  .endpoint-grid {
    grid-template-columns: 1fr;
  }
}
</style>
