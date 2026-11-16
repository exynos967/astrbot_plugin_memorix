<script setup lang="ts">
// Tab 3：检索参数自动调优（POST /v1/tuning）—— 线上参数/TOML 导出回退、创建调优任务、留出集 Baseline->Best(Δ) 对比与报告查看。
import { computed, onBeforeUnmount, onMounted } from "vue";
import { copyTextSafe } from "@/services/api";
import { useAppStore } from "@/stores/app";
import { useGovernanceStore } from "@/stores/governance";

const store = useGovernanceStore();
const app = useAppStore();

onMounted(() => {
  void store.loadTuningWorkspace();
});

onBeforeUnmount(() => {
  store.stopTuningPoll();
});

const profileEntries = computed(() => {
  const p = store.currentProfile;
  if (!p || typeof p !== "object") return [];
  const params =
    (p.params as Record<string, unknown> | undefined) ||
    (p.parameters as Record<string, unknown> | undefined) ||
    p;
  return Object.entries(params)
    .filter(([, v]) => typeof v === "number" || typeof v === "string" || typeof v === "boolean")
    .slice(0, 16);
});

interface MetricRow {
  metric: string;
  baseline: number;
  best: number;
  delta: number;
}

const holdoutRows = computed<MetricRow[]>(() => {
  const t = store.selectedTuningTask;
  if (!t) return [];
  const baseMap = t.holdout_validation?.baseline ?? t.baseline_metrics ?? {};
  const bestMap = t.holdout_validation?.best ?? t.best_metrics ?? {};
  const keys = Array.from(new Set([...Object.keys(baseMap), ...Object.keys(bestMap)]));
  return keys
    .filter((k) => typeof baseMap[k] === "number" || typeof bestMap[k] === "number")
    .map((k) => {
      const base = Number(baseMap[k] ?? 0);
      const best = Number(bestMap[k] ?? base);
      const delta =
        typeof t.holdout_validation?.delta?.[k] === "number"
          ? Number(t.holdout_validation.delta[k])
          : best - base;
      return { metric: k, baseline: base, best, delta };
    });
});

const isSelectedRunning = computed(() => {
  const st = store.selectedTuningTask?.status;
  return st === "queued" || st === "running" || st === "pending" || st === "cancel_requested";
});

const selectedProgressPct = computed(() => {
  const t = store.selectedTuningTask;
  if (!t) return 0;
  if (typeof t.progress === "number") {
    return Math.min(100, Math.round(t.progress <= 1 ? t.progress * 100 : t.progress));
  }
  if (t.current_round && t.total_rounds) {
    return Math.min(100, Math.round((t.current_round / t.total_rounds) * 100));
  }
  return t.status === "completed" || t.status === "succeeded" ? 100 : 15;
});

async function switchProfileView(mode: "summary" | "toml"): Promise<void> {
  store.profileViewMode = mode;
  if (mode === "toml" && !store.exportedToml) {
    await store.loadExportedToml();
  }
}

async function copyToml(): Promise<void> {
  if (!store.exportedToml) {
    await store.loadExportedToml();
  }
  if (!store.exportedToml) return;
  const ok = await copyTextSafe(store.exportedToml);
  if (ok) app.pushSuccess("TOML 调优配置已复制到剪贴板", "检索调优");
}

async function copyMarkdownReport(): Promise<void> {
  if (!store.tuningReportMd) return;
  const ok = await copyTextSafe(store.tuningReportMd);
  if (ok) app.pushSuccess("调优报告已复制", "检索调优");
}

function formatNum(n: number): string {
  return Number.isFinite(n) ? n.toFixed(4) : "-";
}

function formatDelta(d: number): string {
  if (!Number.isFinite(d)) return "-";
  return `${d >= 0 ? "+" : ""}${d.toFixed(4)}`;
}
</script>

<template>
  <div class="tuning-tab">
    <!-- 1. 当前线上检索参数画像与 TOML 导出/回退 -->
    <div class="band">
      <div class="panel-title">
        <div>
          <h2>当前检索参数快照</h2>
          <span class="section-label">查看线上混合检索权重与阈值，支持复制 TOML 或回退至上一快照</span>
        </div>
        <div class="profile-actions">
          <div class="segmented" role="group" aria-label="参数展示视图">
            <button
              type="button"
              :class="{ active: store.profileViewMode === 'summary' }"
              @click="switchProfileView('summary')"
            >
              参数摘要
            </button>
            <button
              type="button"
              :class="{ active: store.profileViewMode === 'toml' }"
              @click="switchProfileView('toml')"
            >
              TOML 视图
            </button>
          </div>
          <button class="btn sm" type="button" @click="copyToml">复制 TOML</button>
          <button
            class="btn danger sm"
            type="button"
            :disabled="store.tuningBusy"
            @click="store.rollbackCurrentProfile()"
          >
            回退上一快照
          </button>
        </div>
      </div>

      <div v-if="store.profileViewMode === 'toml'">
        <pre class="json mono">{{ store.exportedToml || "正在生成 TOML 配置…" }}</pre>
      </div>
      <div v-else-if="profileEntries.length" class="profile-grid">
        <div v-for="[key, val] in profileEntries" :key="key" class="memory-config-item">
          <span class="mono">{{ key }}</span>
          <strong class="mono tabular-nums">{{ String(val) }}</strong>
        </div>
      </div>
      <div v-else class="empty-state">当前使用默认检索基准参数</div>
    </div>

    <!-- 2. 新建调优任务 + 任务列表与留出集指标评估 -->
    <div class="tuning-workbench">
      <div class="band tuning-left">
        <div class="panel-title">
          <div>
            <h2>发起调优实验</h2>
            <span class="section-label">基于留出验证集自动搜索最优混合检索超参数</span>
          </div>
        </div>

        <div class="field">
          <label for="tuning-objective">优化目标偏好</label>
          <select id="tuning-objective" v-model="store.createTuningForm.objective" class="select">
            <option value="balanced">综合均衡（balanced · F1 / MRR 平衡）</option>
            <option value="precision_priority">准确率优先（precision_priority · 抑制噪声）</option>
            <option value="recall_priority">召回率优先（recall_priority · 广泛联想）</option>
          </select>
        </div>

        <div class="grid-2">
          <div class="field">
            <label for="tuning-intensity">搜索强度</label>
            <select id="tuning-intensity" v-model="store.createTuningForm.intensity" class="select">
              <option value="quick">快速探索（quick）</option>
              <option value="standard">标准搜索（standard）</option>
              <option value="deep">深度寻优（deep）</option>
            </select>
          </div>
          <div class="field">
            <label for="tuning-sample-size">评估样本数</label>
            <input
              id="tuning-sample-size"
              v-model.number="store.createTuningForm.sampleSize"
              class="input tabular-nums"
              type="number"
              min="5"
              max="500"
            />
          </div>
        </div>

        <label class="llm-check">
          <input v-model="store.createTuningForm.llmEnabled" type="checkbox" />
          <span>启用 LLM 辅助相关性裁判（精度更高，消耗少量模型调用）</span>
        </label>

        <button
          class="btn primary"
          type="button"
          :disabled="store.tuningBusy"
          @click="store.startTuningTask()"
        >
          {{ store.tuningBusy ? "正在提交…" : "启动调优任务" }}
        </button>

        <div class="tasks-divider" />

        <div class="panel-title" style="margin-bottom: 4px">
          <h3 class="tabular-nums">调优任务队列（{{ store.tuningTasks.length }}）</h3>
          <button class="btn sm" type="button" @click="store.loadTuningWorkspace()">刷新队列</button>
        </div>

        <div v-if="store.tuningTasks.length" class="tuning-task-list">
          <button
            v-for="t in store.tuningTasks"
            :key="t.task_id"
            type="button"
            class="tuning-task-row"
            :class="{ active: store.selectedTuningTask?.task_id === t.task_id }"
            @click="store.selectTuningTask(t.task_id)"
          >
            <div class="task-row-head">
              <strong class="mono">{{ t.task_id }}</strong>
              <span
                class="tag"
                :class="
                  t.status === 'completed' || t.status === 'succeeded'
                    ? 'ok'
                    : t.status === 'failed'
                      ? 'bad'
                      : 'warn'
                "
              >
                {{ t.status }}
              </span>
            </div>
            <div class="tags">
              <span class="tag">{{ t.objective || "balanced" }}</span>
              <span class="tag">{{ t.intensity || "standard" }}</span>
              <span v-if="t.applied" class="tag teal">已应用线上</span>
            </div>
          </button>
        </div>
        <div v-else class="empty-state">暂无历史调优任务</div>
      </div>

      <!-- 右侧：任务实验详情、Holdout 验证对比表、应用最优与报告查看 -->
      <div class="band tuning-right">
        <template v-if="store.selectedTuningTask">
          <div class="panel-title">
            <div>
              <h2>实验评估详情 · <span class="mono">{{ store.selectedTuningTask.task_id }}</span></h2>
              <span class="section-label tabular-nums">
                目标：{{ store.selectedTuningTask.objective }} · 强度：{{ store.selectedTuningTask.intensity }} · 样本数：{{ store.selectedTuningTask.sample_size ?? "-" }}
              </span>
            </div>
            <div class="detail-btn-row">
              <button
                v-if="isSelectedRunning"
                class="btn danger sm"
                type="button"
                :disabled="store.tuningBusy"
                @click="store.cancelTuningTask(store.selectedTuningTask.task_id)"
              >
                取消任务
              </button>
              <button
                class="btn sm"
                type="button"
                :disabled="store.reportLoading"
                @click="store.fetchTaskMarkdownReport(store.selectedTuningTask.task_id)"
              >
                {{ store.reportLoading ? "读取中…" : "加载实验报告" }}
              </button>
              <button
                class="btn primary sm"
                type="button"
                :disabled="store.tuningBusy || isSelectedRunning"
                @click="store.applyBestTuning(store.selectedTuningTask.task_id)"
              >
                应用推荐参数
              </button>
            </div>
          </div>

          <div v-if="isSelectedRunning" class="running-progress-strip">
            <div class="running-head">
              <strong>正在执行超参寻优…</strong>
              <span class="mono tabular-nums">
                {{
                  store.selectedTuningTask.current_round
                    ? `轮次 ${store.selectedTuningTask.current_round}/${store.selectedTuningTask.total_rounds ?? "?"}`
                    : `${selectedProgressPct}%`
                }}
              </span>
            </div>
            <div class="progress-track">
              <div class="progress-fill" :style="{ width: `${selectedProgressPct}%` }" />
            </div>
          </div>

          <p v-if="store.selectedTuningTask.error" class="task-err">
            {{ store.selectedTuningTask.error }}
          </p>

          <!-- Holdout Validation 指标对比表 -->
          <div class="holdout-section">
            <div class="holdout-head">
              <h3>留出集验证指标对比（Baseline → Best Δ）</h3>
              <span
                v-if="typeof store.selectedTuningTask.holdout_validation?.passed === 'boolean'"
                class="tag"
                :class="store.selectedTuningTask.holdout_validation.passed ? 'ok' : 'warn'"
              >
                {{
                  store.selectedTuningTask.holdout_validation.passed
                    ? "通过防退化检验"
                    : "未显著超越基线"
                }}
              </span>
            </div>

            <div v-if="holdoutRows.length" class="metrics-table-wrap">
              <table class="metrics-table tabular-nums">
                <thead>
                  <tr>
                    <th>评估指标</th>
                    <th class="num-col">基线（Baseline）</th>
                    <th class="num-col">最优（Best）</th>
                    <th class="num-col">增益（Δ）</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in holdoutRows" :key="row.metric">
                    <td class="mono">{{ row.metric }}</td>
                    <td class="mono num-col">{{ formatNum(row.baseline) }}</td>
                    <td class="mono num-col"><strong>{{ formatNum(row.best) }}</strong></td>
                    <td class="mono num-col">
                      <span
                        class="tag"
                        :class="row.delta > 0 ? 'ok' : row.delta < 0 ? 'bad' : ''"
                      >
                        {{ formatDelta(row.delta) }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="empty-state">
              {{ isSelectedRunning ? "等待首轮验证指标产出…" : "该任务暂无细项指标对比数据" }}
            </div>
          </div>

          <div v-if="store.tuningReportMd" class="report-section">
            <div class="panel-title" style="margin-bottom: 8px">
              <h3>Markdown 实验报告</h3>
              <button class="btn sm" type="button" @click="copyMarkdownReport">复制报告</button>
            </div>
            <pre class="json report-pre">{{ store.tuningReportMd }}</pre>
          </div>
        </template>

        <div v-else class="empty-state">
          <strong>请从左侧创建或选择调优任务</strong>
          <p>支持查看留出验证集 Baseline → Best 增益对比、阅读完整实验报告，并将推荐参数热更新至线上。</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tabular-nums {
  font-variant-numeric: tabular-nums;
}

.tuning-tab {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.profile-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.profile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(175px, 1fr));
  gap: 8px;
}

.tuning-workbench {
  display: grid;
  grid-template-columns: minmax(280px, 0.85fr) minmax(0, 1.15fr);
  gap: 16px;
  align-items: start;
}

.tuning-left,
.tuning-right {
  margin-top: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.llm-check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text);
  cursor: pointer;
}

.llm-check input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
}

.tasks-divider {
  height: 1px;
  background: var(--hairline);
  margin: 4px 0;
}

.tuning-task-list {
  display: flex;
  flex-direction: column;
  max-height: 300px;
  overflow-y: auto;
  border-top: 1px solid var(--hairline);
}

.tuning-task-row {
  width: 100%;
  text-align: left;
  padding: 10px;
  border: 0;
  border-bottom: 1px solid var(--hairline);
  border-left: 2px solid transparent;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.tuning-task-row:hover {
  background: var(--nav-soft);
}

.tuning-task-row.active {
  border-left-color: var(--accent);
  background: var(--accent-soft);
}

.task-row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.detail-btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.running-progress-strip {
  padding: 10px 0;
  border-top: 1px solid var(--hairline);
  border-bottom: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.running-head {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  color: var(--ink);
}

.task-err {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--red-soft);
  color: var(--red);
  font-size: 12.5px;
}

.holdout-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.holdout-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.holdout-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 680;
  color: var(--ink);
}

.metrics-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.metrics-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.metrics-table th,
.metrics-table td {
  padding: 9px 12px;
  text-align: left;
  border-bottom: 1px solid var(--hairline);
}

.metrics-table .num-col {
  text-align: right;
}

.metrics-table th {
  font-size: 12px;
  color: var(--muted);
  background: var(--surface-soft);
}

.metrics-table tr:last-child td {
  border-bottom: 0;
}

.report-section {
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.report-pre {
  max-height: 360px;
  font-family: inherit;
}

@media (max-width: 980px) {
  .tuning-workbench {
    grid-template-columns: 1fr;
  }
}
</style>
