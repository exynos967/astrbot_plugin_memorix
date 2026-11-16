<script setup lang="ts">
// 人物别名覆盖与 v2.0 结构化事实账本（FactClaim 全字段支持：极性/6分区/稳定性/单多值/置信度 + 状态筛选 + 二次确认）。
import { computed, nextTick, ref, watch } from "vue";
import { usePeopleStore } from "@/stores/people";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";
import {
  callPersonAliasesAdmin,
  callPersonFactsAdmin,
  type PersonAliasDetails,
  type PersonFactClaim,
} from "@/services/personApi";
import {
  FACT_CARDINALITY_OPTIONS,
  FACT_POLARITY_OPTIONS,
  FACT_PROFILE_SECTIONS,
  FACT_STABILITY_OPTIONS,
  factSectionLabel,
  factStabilityLabel,
  factStatusLabel,
} from "@/utils/personText";

interface FactListResponse {
  success?: boolean;
  error?: string;
  items: PersonFactClaim[];
}

type StatusFilter = "all" | "active" | "conflicted" | "superseded" | "retracted";

const STATUS_TABS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "active", label: "生效中" },
  { value: "conflicted", label: "冲突待决" },
  { value: "superseded", label: "已被替代" },
  { value: "retracted", label: "已撤回" },
];

const people = usePeopleStore();
const graph = useGraphStore();
const app = useAppStore();
const logs = useLogsStore();

const editorRef = ref<HTMLElement | null>(null);
const aliases = ref("");
const hasManualAliases = ref(false);
const derived = ref<string[]>([]);
const facts = ref<PersonFactClaim[]>([]);
const statusFilter = ref<StatusFilter>("all");

const busy = ref(false);
const editing = ref("");
const key = ref("");
const value = ref("");
const polarity = ref<"positive" | "negative">("positive");
const profileSection = ref<string>("stable_facts");
const stability = ref<string>("stable");
const cardinality = ref<string>("set");
const confidence = ref<number>(0.95);

let revision = 0;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

const statusCounts = computed(() => {
  const counts: Record<StatusFilter, number> = {
    all: facts.value.length,
    active: 0,
    conflicted: 0,
    superseded: 0,
    retracted: 0,
  };
  for (const f of facts.value) {
    if (f.status in counts) {
      counts[f.status as StatusFilter] += 1;
    }
  }
  return counts;
});

const filteredFacts = computed(() => {
  if (statusFilter.value === "all") return facts.value;
  return facts.value.filter((f) => f.status === statusFilter.value);
});

function resetEditor(): void {
  editing.value = "";
  key.value = "";
  value.value = "";
  polarity.value = "positive";
  profileSection.value = "stable_facts";
  stability.value = "stable";
  cardinality.value = "set";
  confidence.value = 0.95;
}

async function reload(logManual = false): Promise<void> {
  const seq = ++revision;
  const person = people.overrideId.trim();
  const scope = graph.effectiveScope();
  facts.value = [];
  aliases.value = "";
  hasManualAliases.value = false;
  derived.value = [];
  if (!person) return;

  try {
    const [aliasResult, factResult] = await Promise.all([
      callPersonAliasesAdmin<PersonAliasDetails>("get_aliases", { person_id: person }, scope),
      callPersonFactsAdmin<FactListResponse>(
        "list",
        {
          scope_type: "person",
          scope_id: person,
          statuses: ["active", "conflicted", "superseded", "retracted"],
          limit: 200,
        },
        scope,
      ),
    ]);
    if (seq !== revision) return;
    hasManualAliases.value = aliasResult.manual_aliases !== null && aliasResult.manual_aliases !== undefined;
    aliases.value = (aliasResult.manual_aliases || []).join("\n");
    derived.value = aliasResult.derived_aliases || [];
    facts.value = factResult.items || [];
    if (logManual) {
      logs.log(`已刷新「${person}」的别名与事实账本（共 ${facts.value.length} 条）`, "info", "人物事实");
    }
  } catch (error) {
    if (seq === revision) app.pushError(errText(error), "人物事实");
  }
}

async function saveAliases(): Promise<void> {
  const person = people.overrideId.trim();
  if (busy.value || !person) return;
  const parsed = aliases.value
    .split(/[\r\n]+/)
    .map((s) => s.trim())
    .filter(Boolean);

  const scope = graph.effectiveScope();
  busy.value = true;
  try {
    await callPersonAliasesAdmin(
      "set_aliases",
      { person_id: person, aliases: parsed },
      scope,
    );
    if (person === people.overrideId.trim() && scope === graph.effectiveScope()) {
      app.pushSuccess(
        parsed.length
          ? `已保存「${person}」的人工别名（${parsed.length} 项）`
          : `已清空「${person}」的人工别名`,
        "人物别名",
      );
      await reload();
      void people.query(true, person);
    }
  } catch (error) {
    app.pushError(errText(error), "人物别名");
  } finally {
    busy.value = false;
  }
}

async function restoreDerivedAliases(): Promise<void> {
  const person = people.overrideId.trim();
  if (busy.value || !person) return;
  const confirmed = await app.requestConfirmation({
    title: "恢复自动推导别名",
    message: `确定删除「${person}」的人工别名覆盖，恢复使用系统从对话中推导的自动别名吗？`,
    confirmText: "恢复自动别名",
    cancelText: "取消",
    danger: true,
  });
  if (!confirmed) return;

  const scope = graph.effectiveScope();
  busy.value = true;
  try {
    await callPersonAliasesAdmin("delete_aliases", { person_id: person }, scope);
    if (person === people.overrideId.trim() && scope === graph.effectiveScope()) {
      app.pushSuccess(`已恢复「${person}」的自动推导别名`, "人物别名");
      await reload();
      void people.query(true, person);
    }
  } catch (error) {
    app.pushError(errText(error), "人物别名");
  } finally {
    busy.value = false;
  }
}

function edit(fact: PersonFactClaim): void {
  editing.value = fact.claim_id;
  key.value = fact.fact_key || "";
  value.value = fact.value_text || "";
  polarity.value = fact.polarity === "negative" ? "negative" : "positive";
  profileSection.value = fact.profile_section || "stable_facts";
  stability.value = fact.stability || "stable";
  cardinality.value = fact.cardinality || "set";
  confidence.value =
    typeof fact.confidence === "number" && !Number.isNaN(fact.confidence)
      ? Math.round(fact.confidence * 100) / 100
      : 0.95;

  void nextTick(() => {
    editorRef.value?.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "nearest",
    });
  });
}

async function saveFact(): Promise<void> {
  const person = people.overrideId.trim();
  if (busy.value || !person) return;
  const trimmedKey = key.value.trim();
  const trimmedVal = value.value.trim();
  if (!trimmedKey || !trimmedVal) {
    app.pushError("请填写事实类别与事实内容", "人物事实");
    return;
  }

  const safeConf = Math.max(0, Math.min(1, Number(confidence.value) || 0.95));
  const isUpdate = Boolean(editing.value);
  const scope = graph.effectiveScope();
  busy.value = true;
  try {
    await callPersonFactsAdmin(
      isUpdate ? "update" : "create",
      {
        ...(isUpdate ? { claim_id: editing.value } : {}),
        scope_type: "person",
        scope_id: person,
        fact_key: trimmedKey,
        value_text: trimmedVal,
        polarity: polarity.value,
        profile_section: profileSection.value,
        stability: stability.value,
        cardinality: cardinality.value,
        confidence: safeConf,
        authority: "manual",
        reason: "astrbot_webui_manual_edit",
      },
      scope,
    );
    if (person === people.overrideId.trim() && scope === graph.effectiveScope()) {
      app.pushSuccess(
        `${isUpdate ? "已更新" : "已写入"}事实「${trimmedKey}：${trimmedVal}」`,
        "人物事实",
      );
      resetEditor();
      await reload();
      void people.query(true, person);
    }
  } catch (error) {
    app.pushError(errText(error), "人物事实");
  } finally {
    busy.value = false;
  }
}

async function retractFact(fact: PersonFactClaim): Promise<void> {
  const person = people.overrideId.trim();
  if (busy.value || !person) return;
  const confirmed = await app.requestConfirmation({
    title: "撤回人物事实",
    message: `确定撤回事实「${fact.fact_key}：${fact.value_text}」吗？撤回后该事实不再参与人物画像生成（后续可随时恢复）。`,
    confirmText: "撤回事实",
    cancelText: "取消",
    danger: true,
  });
  if (!confirmed) return;

  const scope = graph.effectiveScope();
  busy.value = true;
  try {
    await callPersonFactsAdmin(
      "retract",
      { claim_id: fact.claim_id, reason: "astrbot_webui_manual_retract" },
      scope,
    );
    if (person === people.overrideId.trim() && scope === graph.effectiveScope()) {
      if (editing.value === fact.claim_id) resetEditor();
      app.pushSuccess(`已撤回事实「${fact.fact_key}」`, "人物事实");
      await reload();
      void people.query(true, person);
    }
  } catch (error) {
    app.pushError(errText(error), "人物事实");
  } finally {
    busy.value = false;
  }
}

async function restoreFact(fact: PersonFactClaim): Promise<void> {
  const person = people.overrideId.trim();
  if (busy.value || !person) return;
  const scope = graph.effectiveScope();
  busy.value = true;
  try {
    await callPersonFactsAdmin(
      "restore",
      { claim_id: fact.claim_id, reason: "astrbot_webui_manual_restore" },
      scope,
    );
    if (person === people.overrideId.trim() && scope === graph.effectiveScope()) {
      app.pushSuccess(`已恢复事实「${fact.fact_key}」`, "人物事实");
      await reload();
      void people.query(true, person);
    }
  } catch (error) {
    app.pushError(errText(error), "人物事实");
  } finally {
    busy.value = false;
  }
}

function statusBadgeClass(status: string): string {
  if (status === "active") return "ok";
  if (status === "conflicted") return "warn";
  if (status === "retracted") return "bad";
  return "";
}

watch(
  () => [people.overrideId, graph.effectiveScope()],
  () => {
    resetEditor();
    statusFilter.value = "all";
    void reload();
  },
  { immediate: true },
);
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>人物别名与结构化事实账本</h2>
        <span class="section-label">管理别名映射与 FactClaim 六大分区结构化事实</span>
      </div>
      <div class="tags" style="margin-top: 0">
        <span v-if="people.overrideId" class="tag mono">{{ people.overrideId }}</span>
        <span v-else class="tag">请先选择人物</span>
      </div>
    </div>

    <fieldset :disabled="busy || !people.overrideId.trim()" class="memory-fields">
      <!-- 1. 别名管理区 -->
      <div class="alias-section">
        <div class="alias-head">
          <label for="manual-aliases">人工别名覆盖（每行一个；留空保存清空别名）</label>
          <span class="tag" :class="hasManualAliases ? 'warn' : 'ok'">
            {{ hasManualAliases ? "人工别名生效中" : "自动推导别名生效中" }}
          </span>
        </div>
        <textarea
          id="manual-aliases"
          v-model="aliases"
          class="textarea"
          rows="3"
          placeholder="每行填写一个常用称呼或群昵称…"
        ></textarea>
        <p class="derived-hint">
          系统自动推导别名：{{ derived.length ? derived.join("、") : "暂无" }}
        </p>
        <div class="toolbar">
          <button class="btn" type="button" @click="saveAliases">
            保存人工别名
          </button>
          <button
            class="btn"
            type="button"
            :disabled="!hasManualAliases"
            @click="restoreDerivedAliases"
          >
            恢复自动别名
          </button>
          <button class="btn" type="button" @click="reload(true)">
            刷新账本
          </button>
        </div>
      </div>

      <!-- 2. 结构化事实编辑器（扁平分割区） -->
      <div ref="editorRef" class="fact-editor" :class="{ 'editor-editing': !!editing }">
        <div class="fact-editor-head">
          <h3>{{ editing ? "编辑结构化事实" : "新增结构化事实（FactClaim）" }}</h3>
          <span v-if="editing" class="tag warn mono">正在编辑：{{ editing }}</span>
        </div>
        <div class="grid-3">
          <div class="field">
            <label for="fact-key">事实类别（fact_key）</label>
            <input
              id="fact-key"
              v-model="key"
              class="input"
              placeholder="例如：饮食偏好 / 职业 / 禁忌"
            />
          </div>
          <div class="field">
            <label for="fact-value">事实内容（value_text）</label>
            <input
              id="fact-value"
              v-model="value"
              class="input"
              placeholder="例如：喜欢黑咖啡 / 讨厌香菜"
            />
          </div>
          <div class="field">
            <label for="fact-polarity">事实极性（polarity）</label>
            <select id="fact-polarity" v-model="polarity" class="select">
              <option
                v-for="opt in FACT_POLARITY_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="fact-section">画像分区（profile_section）</label>
            <select id="fact-section" v-model="profileSection" class="select">
              <option
                v-for="sec in FACT_PROFILE_SECTIONS"
                :key="sec.value"
                :value="sec.value"
              >
                {{ sec.label }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="fact-stability">稳定性（stability）</label>
            <select id="fact-stability" v-model="stability" class="select">
              <option
                v-for="opt in FACT_STABILITY_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="fact-cardinality">同类基数（cardinality）</label>
            <select id="fact-cardinality" v-model="cardinality" class="select">
              <option
                v-for="opt in FACT_CARDINALITY_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="fact-confidence">置信度（0 ~ 1）</label>
            <input
              id="fact-confidence"
              v-model.number="confidence"
              class="input tabular"
              type="number"
              min="0"
              max="1"
              step="0.05"
            />
          </div>
        </div>
        <div class="toolbar" style="margin-top: 10px">
          <button
            class="btn primary"
            type="button"
            :disabled="!key.trim() || !value.trim()"
            @click="saveFact"
          >
            {{ editing ? "保存事实修改" : "写入事实账本" }}
          </button>
          <button v-if="editing" class="btn" type="button" @click="resetEditor">
            取消编辑
          </button>
        </div>
      </div>

      <!-- 3. 状态筛选栏 + 细线事实列表 -->
      <div class="fact-ledger-section">
        <div class="fact-filter-bar">
          <div class="segmented" role="tablist" aria-label="事实状态筛选">
            <button
              v-for="tab in STATUS_TABS"
              :key="tab.value"
              type="button"
              role="tab"
              :aria-selected="statusFilter === tab.value"
              :class="{ active: statusFilter === tab.value }"
              @click="statusFilter = tab.value"
            >
              <span class="tabular">{{ tab.label }}（{{ statusCounts[tab.value] }}）</span>
            </button>
          </div>
        </div>

        <div v-if="filteredFacts.length" class="rows fact-list">
          <article
            v-for="fact in filteredFacts"
            :key="fact.claim_id"
            class="fact-row"
            :class="{ 'is-editing': editing === fact.claim_id }"
          >
            <div class="fact-item-main">
              <div class="fact-header">
                <span
                  v-if="fact.polarity === 'negative'"
                  class="tag bad"
                  title="否定极性事实：明确表示排斥、不具备或禁止"
                >
                  否定
                </span>
                <span v-else class="tag ok">肯定</span>
                <strong class="fact-key">{{ fact.fact_key }}</strong>
                <span class="fact-sep">：</span>
                <span class="fact-val">{{ fact.value_text }}</span>
              </div>
              <div class="tags fact-meta">
                <span class="tag" :class="statusBadgeClass(fact.status)">
                  {{ factStatusLabel(fact.status) }}
                </span>
                <span class="tag">{{ factSectionLabel(fact.profile_section) }}</span>
                <span class="tag">{{ factStabilityLabel(fact.stability) }}</span>
                <span class="tag">
                  {{ fact.cardinality === "single" ? "单值互斥" : "多值并存" }}
                </span>
                <span v-if="fact.confidence != null" class="tag mono tabular">
                  置信 {{ Math.round(fact.confidence * 100) }}%
                </span>
                <span v-if="fact.authority" class="tag mono">{{ fact.authority }}</span>
              </div>
            </div>

            <div class="toolbar fact-actions">
              <button class="btn sm" type="button" @click="edit(fact)">
                {{ editing === fact.claim_id ? "编辑中" : "编辑事实" }}
              </button>
              <button
                v-if="['active', 'conflicted'].includes(fact.status)"
                class="btn sm danger"
                type="button"
                @click="retractFact(fact)"
              >
                撤回事实
              </button>
              <button
                v-else
                class="btn sm"
                type="button"
                @click="restoreFact(fact)"
              >
                恢复生效
              </button>
            </div>
          </article>
        </div>
        <div v-else class="empty">
          {{
            !people.overrideId.trim()
              ? "请先在上方或左侧目录选择人物"
              : statusFilter === "all"
                ? "当前人物暂无结构化事实记录"
                : "该筛选状态下暂无事实记录"
          }}
        </div>
      </div>
    </fieldset>
  </div>
</template>

<style scoped>
.memory-fields {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.alias-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.alias-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.alias-head label {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--muted);
}

.derived-hint {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.fact-editor {
  padding-top: 16px;
  border-top: 1px solid var(--hairline);
}

.fact-editor.editor-editing {
  padding-left: 10px;
  border-left: 2px solid var(--accent);
}

.fact-editor-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.fact-editor-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 680;
  color: var(--ink);
}

.fact-ledger-section {
  padding-top: 16px;
  border-top: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.fact-filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.fact-list {
  border-top: 1px solid var(--hairline);
}

.fact-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 10px;
  padding: 11px 4px 11px 8px;
  border-left: 2px solid transparent;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.fact-row.is-editing {
  border-left-color: var(--accent);
  background: var(--accent-soft);
}

.fact-item-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1 1 300px;
  min-width: 0;
}

.fact-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13.5px;
}

.fact-key {
  color: var(--ink);
}

.fact-val {
  color: var(--text);
  word-break: break-word;
}

.fact-meta {
  gap: 6px;
}

.fact-actions {
  margin-top: 0;
  align-items: center;
}
</style>
