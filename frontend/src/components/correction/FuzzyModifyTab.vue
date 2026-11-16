<script setup lang="ts">
// Tab 1：自然语言模糊修正（POST /v1/fuzzy_modify）
import { computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useGovernanceStore } from "@/stores/governance";

const store = useGovernanceStore();
const router = useRouter();

onMounted(() => {
  void store.loadFuzzyPlans();
});

const cascadeCounts = computed(() => store.activePreview?.cascade_preview?.counts ?? null);
const operations = computed(() => store.activePreview?.operations ?? []);
const candidates = computed(() => store.activePreview?.candidates ?? []);

const canExecuteActive = computed(() => {
  const st = store.activePlan?.status;
  return Boolean(
    store.activePlan?.plan_id &&
      (!st || st === "previewed" || st === "pending" || st === "ready"),
  );
});

const canRollbackActive = computed(() => {
  const st = store.activePlan?.status;
  return Boolean(
    store.activePlan?.plan_id && (st === "executed" || st === "completed" || st === "succeeded"),
  );
});

function formatItemSummary(item: Record<string, unknown>): string {
  const preferred =
    item.summary ||
    item.description ||
    item.content ||
    item.text ||
    item.fact ||
    item.action ||
    item.operation ||
    item.op ||
    item.target_id ||
    item.id;
  if (typeof preferred === "string" && preferred.trim()) return preferred;
  return JSON.stringify(item);
}

function formatItemSub(item: Record<string, unknown>): string {
  const parts: string[] = [];
  if (item.op || item.action || item.type) parts.push(String(item.op || item.action || item.type));
  if (item.field) parts.push(`字段:${item.field}`);
  if (item.target_type) parts.push(String(item.target_type));
  if (typeof item.score === "number") parts.push(`相关度 ${item.score.toFixed(2)}`);
  return parts.join(" · ");
}

function statusTagClass(st?: string): string {
  if (st === "executed" || st === "completed") return "ok";
  if (st === "rolled_back") return "teal";
  if (st === "failed" || st === "stale") return "bad";
  return "info";
}

function statusText(st?: string): string {
  const map: Record<string, string> = {
    previewed: "待执行",
    pending: "待执行",
    ready: "待执行",
    executed: "已执行",
    completed: "已执行",
    rolled_back: "已回滚",
    failed: "失败",
    stale: "超时过期",
  };
  return (st && map[st]) || st || "待执行";
}

function formatTime(ts?: number | string): string {
  if (!ts) return "-";
  const num = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(String(ts));
  if (Number.isNaN(num)) return String(ts);
  return new Date(num).toLocaleString("zh-CN", { hour12: false });
}
</script>

<template>
  <div class="fuzzy-tab">
    <!-- 未启用时的跳转横幅（深链跳转至运行配置 fuzzy_modify 分组） -->
    <div v-if="store.fuzzyDisabled" class="notice warn">
      <div>
        <strong>自然语言模糊修正尚未启用</strong>
        <p class="guide-sub">
          前往「运行配置」开启 <code class="mono">integration.fuzzy_modify.enabled</code> 后，即可生成修正计划预览与级联纠偏。
        </p>
      </div>
      <button
        class="btn primary"
        type="button"
        @click="router.push({ path: '/settings', query: { group: 'fuzzy_modify' } })"
      >
        前往配置模糊修正
      </button>
    </div>

    <!-- 双栏工作区：左侧修正指令表单 + 右侧影响预览与执行舞台 -->
    <div class="fuzzy-workbench">
      <div class="band fuzzy-form-card">
        <div class="panel-title">
          <div>
            <h2>生成修正计划</h2>
            <span class="section-label">先生成只读计划预览，核对级联影响后再执行写入</span>
          </div>
        </div>

        <div class="field">
          <label>修正目标范围</label>
          <div class="segmented" role="group" aria-label="修正目标范围">
            <button
              type="button"
              :class="{ active: store.fuzzyForm.targetScope === 'person_profile' }"
              @click="store.fuzzyForm.targetScope = 'person_profile'"
            >
              人物画像（person_profile）
            </button>
            <button
              type="button"
              :class="{ active: store.fuzzyForm.targetScope === 'memory' }"
              @click="store.fuzzyForm.targetScope = 'memory'"
            >
              记忆与关系（memory）
            </button>
          </div>
        </div>

        <div class="field">
          <label for="fuzzy-request-text">自然语言修正指令</label>
          <textarea
            id="fuzzy-request-text"
            v-model="store.fuzzyForm.requestText"
            class="textarea"
            rows="3"
            placeholder="例如：把小明住在上海的旧记录改为已搬去杭州工作，并停用冲突关系…"
          />
        </div>

        <div class="grid-2">
          <div class="field">
            <div class="label-inline">
              <label :for="store.fuzzyForm.personLookupMode === 'keyword' ? 'fuzzy-person-kw' : 'fuzzy-person-id'">
                目标人物定位
              </label>
              <button
                type="button"
                class="inline-switch"
                @click="
                  store.fuzzyForm.personLookupMode =
                    store.fuzzyForm.personLookupMode === 'keyword' ? 'id' : 'keyword'
                "
              >
                改按 {{ store.fuzzyForm.personLookupMode === "keyword" ? "Person ID" : "姓名关键词" }}
              </button>
            </div>
            <input
              v-if="store.fuzzyForm.personLookupMode === 'keyword'"
              id="fuzzy-person-kw"
              v-model="store.fuzzyForm.personKeyword"
              class="input"
              placeholder="人物姓名或昵称关键词（可选）"
            />
            <input
              v-else
              id="fuzzy-person-id"
              v-model="store.fuzzyForm.personId"
              class="input mono"
              placeholder="精确 person_id（可选）"
            />
          </div>

          <div class="field">
            <label for="fuzzy-chat-id">限定会话 Chat ID（可选）</label>
            <input
              id="fuzzy-chat-id"
              v-model="store.fuzzyForm.chatId"
              class="input"
              placeholder="留空检索当前作用域"
            />
          </div>
        </div>

        <div class="grid-2">
          <div class="field">
            <label for="fuzzy-limit">候选检索上限</label>
            <input
              id="fuzzy-limit"
              v-model.number="store.fuzzyForm.limit"
              class="input tabular-nums"
              type="number"
              min="1"
              max="50"
            />
          </div>
          <div class="field">
            <label for="fuzzy-reason">修正备注（可选）</label>
            <input
              id="fuzzy-reason"
              v-model="store.fuzzyForm.reason"
              class="input"
              placeholder="例如：管理员手动纠偏"
            />
          </div>
        </div>

        <div class="form-submit-row">
          <button
            class="btn primary"
            type="button"
            :disabled="store.fuzzyBusy || !store.fuzzyForm.requestText.trim()"
            @click="store.generateFuzzyPreview()"
          >
            {{ store.fuzzyBusy ? "正在生成预览…" : "生成修正预览" }}
          </button>
        </div>
      </div>

      <!-- 右侧：修正计划预览与级联影响舞台 -->
      <div class="band fuzzy-stage-card">
        <div class="panel-title">
          <div>
            <h2>计划审阅与级联影响</h2>
            <span class="section-label mono">
              {{
                store.activePlan
                  ? `计划 ID：${store.activePlan.plan_id}`
                  : "提交左侧指令或从下方记录选择计划"
              }}
            </span>
          </div>
          <div v-if="store.activePlan" class="stage-actions">
            <span class="tag" :class="statusTagClass(store.activePlan.status)">
              {{ statusText(store.activePlan.status) }}
            </span>
            <button
              v-if="canExecuteActive"
              class="btn primary sm"
              type="button"
              :disabled="store.fuzzyBusy"
              @click="store.executeFuzzyPlan(store.activePlan.plan_id)"
            >
              执行计划
            </button>
            <button
              v-if="canRollbackActive"
              class="btn danger sm"
              type="button"
              :disabled="store.fuzzyBusy"
              @click="store.rollbackFuzzyPlan(store.activePlan.plan_id)"
            >
              回滚计划
            </button>
          </div>
        </div>

        <template v-if="store.activePlan || store.activePreview">
          <div class="tags" style="margin-top: 0">
            <span
              v-if="typeof (store.activePreview?.confidence ?? store.activePlan?.confidence) === 'number'"
              class="tag info tabular-nums"
            >
              置信度 {{ (((store.activePreview?.confidence ?? store.activePlan?.confidence) || 0) * 100).toFixed(1) }}%
            </span>
            <span v-if="store.activePreview?.scope || store.activePlan?.scope" class="tag mono">
              {{ store.activePreview?.scope || store.activePlan?.scope }}
            </span>
            <span v-if="store.activePreview?.requires_confirmation" class="tag warn tabular-nums">
              需人工确认（阈值 {{ store.activePreview.confirm_threshold ?? "-" }}）
            </span>
          </div>

          <div v-if="cascadeCounts" class="cascade-strip tabular-nums">
            <span class="cascade-title">级联波及：</span>
            <span class="tag info">关系 {{ cascadeCounts.relations ?? 0 }}</span>
            <span class="tag warn">停用旧关系 {{ cascadeCounts.relations_mark_inactive ?? 0 }}</span>
            <span class="tag warn">标记过期证据 {{ cascadeCounts.relations_mark_stale_evidence ?? 0 }}</span>
            <span class="tag ok">受保护跳过 {{ cascadeCounts.relations_skipped_protected ?? 0 }}</span>
            <span class="tag teal">关联实体 {{ cascadeCounts.entities ?? 0 }}</span>
          </div>

          <div class="grid-2 stage-lists">
            <div class="stage-col">
              <h3 class="col-title tabular-nums">拟执行操作（{{ operations.length }}）</h3>
              <div v-if="operations.length" class="hairline-list">
                <div v-for="(op, idx) in operations" :key="idx" class="hairline-row">
                  <span v-if="formatItemSub(op)" class="mini-meta mono tabular-nums">{{ formatItemSub(op) }}</span>
                  <p>{{ formatItemSummary(op) }}</p>
                </div>
              </div>
              <div v-else class="empty-state">无待执行写入项</div>
            </div>

            <div class="stage-col">
              <h3 class="col-title tabular-nums">命中候选证据（{{ candidates.length }}）</h3>
              <div v-if="candidates.length" class="hairline-list">
                <div v-for="(cand, idx) in candidates" :key="idx" class="hairline-row">
                  <span v-if="formatItemSub(cand)" class="mini-meta mono tabular-nums">{{ formatItemSub(cand) }}</span>
                  <p>{{ formatItemSummary(cand) }}</p>
                </div>
              </div>
              <div v-else class="empty-state">无命中候选条目</div>
            </div>
          </div>
        </template>

        <div v-else class="empty-state">
          <strong>尚未选择修正计划</strong>
          <p>在左侧输入自然语言修正需求并生成预览，即可在此审阅拟变更操作与图谱级联影响。</p>
        </div>
      </div>
    </div>

    <!-- 下方：历史修正计划表 -->
    <div class="band">
      <div class="panel-title">
        <div>
          <h2 class="tabular-nums">历史修正计划（{{ store.fuzzyPlans.length }}）</h2>
          <span class="section-label">审阅历史计划详情、执行待定计划或回滚已执行计划</span>
        </div>
        <div class="history-toolbar">
          <select
            v-model="store.fuzzyStatusFilter"
            class="select sm-select"
            aria-label="筛选计划状态"
            @change="store.loadFuzzyPlans()"
          >
            <option value="">全部状态</option>
            <option value="previewed">待执行（previewed）</option>
            <option value="executed">已执行（executed）</option>
            <option value="rolled_back">已回滚（rolled_back）</option>
            <option value="failed">失败（failed）</option>
          </select>
          <button
            class="btn sm"
            type="button"
            :disabled="store.fuzzyBusy"
            @click="store.reconcileFuzzyPlans()"
          >
            清理超时计划
          </button>
          <button
            class="btn sm"
            type="button"
            :disabled="store.fuzzyListLoading"
            @click="store.loadFuzzyPlans()"
          >
            刷新列表
          </button>
        </div>
      </div>

      <div v-if="store.fuzzyPlans.length" class="table-list plan-list-container">
        <div
          v-for="plan in store.fuzzyPlans"
          :key="plan.plan_id"
          class="row-item plan-row"
          :class="{ 'is-selected': store.activePlan?.plan_id === plan.plan_id }"
        >
          <div class="row-main">
            <span class="plan-icon" aria-hidden="true">
              <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12.5 3.5l4 4L7 17H3v-4l9.5-9.5z" />
              </svg>
            </span>
            <div class="plan-main-text">
              <strong>{{ plan.request_text || plan.plan_id }}</strong>
              <span class="mono">{{ plan.plan_id }} · {{ plan.scope || "memory" }}</span>
            </div>
          </div>
          <div class="cell-note tabular-nums">{{ formatTime(plan.created_at || plan.updated_at) }}</div>
          <div>
            <span class="tag" :class="statusTagClass(plan.status)">{{ statusText(plan.status) }}</span>
          </div>
          <div class="plan-row-actions">
            <button class="btn sm" type="button" @click="store.inspectFuzzyPlan(plan.plan_id)">
              审阅计划
            </button>
            <button
              v-if="plan.status === 'previewed' || plan.status === 'pending'"
              class="btn primary sm"
              type="button"
              :disabled="store.fuzzyBusy"
              @click="store.executeFuzzyPlan(plan.plan_id)"
            >
              执行计划
            </button>
            <button
              v-if="plan.status === 'executed' || plan.status === 'completed'"
              class="btn danger sm"
              type="button"
              :disabled="store.fuzzyBusy"
              @click="store.rollbackFuzzyPlan(plan.plan_id)"
            >
              回滚计划
            </button>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">暂无历史模糊修正计划</div>
    </div>
  </div>
</template>

<style scoped>
.fuzzy-tab {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.tabular-nums {
  font-variant-numeric: tabular-nums;
}

.guide-sub {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--muted);
}

.fuzzy-workbench {
  display: grid;
  grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
  gap: 16px;
  align-items: start;
}

.fuzzy-form-card,
.fuzzy-stage-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 0;
}

.label-inline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.inline-switch {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
}

.inline-switch:hover {
  text-decoration: underline;
}

.form-submit-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}

.stage-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.cascade-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
}

.cascade-title {
  font-size: 12px;
  font-weight: 680;
  color: var(--ink);
}

.stage-lists {
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
  align-items: start;
}

.col-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 680;
  color: var(--ink);
}

.hairline-list {
  display: flex;
  flex-direction: column;
  max-height: 290px;
  overflow-y: auto;
  border-top: 1px solid var(--hairline);
}

.hairline-row {
  padding: 9px 2px;
  border-bottom: 1px solid var(--hairline);
}

.mini-meta {
  display: block;
  margin-bottom: 3px;
  font-size: 11.5px;
  font-weight: 650;
  color: var(--accent);
}

.hairline-row p {
  margin: 0;
  font-size: 12.5px;
  color: var(--text);
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.history-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.sm-select {
  width: auto;
  min-height: 32px;
  padding: 4px 10px;
}

.plan-list-container {
  container-type: inline-size;
}

.plan-row {
  grid-template-columns: minmax(180px, 1.6fr) auto auto auto;
}

.plan-row.is-selected {
  background: var(--accent-soft);
}

.plan-icon {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  background: var(--surface-soft);
  color: var(--accent);
  display: inline-grid;
  place-items: center;
  flex: 0 0 auto;
}

.plan-main-text {
  min-width: 0;
}

.plan-row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}

@container (max-width: 680px) {
  .plan-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 8px 12px;
  }

  .plan-row > .row-main {
    grid-column: 1 / -1;
  }

  .plan-row-actions {
    grid-column: 2;
    justify-self: end;
  }
}

@container (max-width: 440px) {
  .plan-row {
    grid-template-columns: 1fr;
  }

  .plan-row-actions {
    grid-column: 1;
    justify-self: start;
    justify-content: flex-start;
  }
}

@media (max-width: 980px) {
  .fuzzy-workbench {
    grid-template-columns: 1fr;
  }
}
</style>
