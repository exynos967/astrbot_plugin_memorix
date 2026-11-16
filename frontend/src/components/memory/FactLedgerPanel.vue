<script setup lang="ts">
// Schema v31 结构化事实台账面板：扁平 1px 细线行列表展示，统一单一主按钮层级，支持肯定/否定极性与 6 大画像分区筛选。
import { computed, ref, watch } from "vue";
import {
  listFacts,
  restoreFactClaim,
  retractFactClaim,
  type FactClaim,
} from "@/services/memoryAdminApi";
import {
  FACT_STATUS_OPTIONS,
  factAuthorityLabel,
  factCardinalityLabel,
  factSectionLabel,
  factStabilityLabel,
  factStatusMeta,
} from "@/utils/memoryText";
import { fetchPersonRegistry } from "@/services/personApi";
import CandidateInput from "@/components/common/CandidateInput.vue";
import FactEditorDialog from "./FactEditorDialog.vue";
import type { CandidateItem } from "@/stores/candidate";
import { useGraphStore } from "@/stores/graph";
import { useMemoryStore } from "@/stores/memory";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import { formatTs } from "@/utils/time";
import { errText } from "@/utils/error";

const graph = useGraphStore();
const memory = useMemoryStore();
const app = useAppStore();
const logs = useLogsStore();

const filterScopeType = ref<"" | "person" | "chat">("");
const filterScopeId = ref("");
const filterStatus = ref<string>("active");
const filterKeyword = ref("");

const items = ref<FactClaim[]>([]);
const loading = ref(false);
const busyClaimId = ref("");

const editorOpen = ref(false);
const editingClaim = ref<FactClaim | null>(null);

const filteredItems = computed(() => {
  const kw = filterKeyword.value.trim().toLowerCase();
  if (!kw) return items.value;
  return items.value.filter((it) =>
    [it.scope_id, it.fact_key, it.value_text, it.profile_section]
      .join(" ")
      .toLowerCase()
      .includes(kw),
  );
});

async function personFilterSource(kw: string): Promise<CandidateItem[]> {
  if (filterScopeType.value === "chat") return [];
  try {
    const res = await fetchPersonRegistry(kw.trim(), 1, 20, graph.effectiveScope());
    return (res.items || [])
      .map((item) => ({
        value: item.person_id || item.display_name || item.person_name || "",
        note:
          (item.display_name || item.person_name) &&
          (item.display_name || item.person_name) !== item.person_id
            ? item.display_name || item.person_name || ""
            : "",
        kind: "人物",
      }))
      .filter((x) => x.value);
  } catch {
    return [];
  }
}

let loadSeq = 0;
async function reload(): Promise<void> {
  const seq = ++loadSeq;
  loading.value = true;
  try {
    const res = await listFacts(
      {
        scope_type: filterScopeType.value,
        scope_id: filterScopeId.value.trim(),
        status: filterStatus.value,
        limit: 200,
      },
      graph.effectiveScope(),
    );
    if (seq === loadSeq) {
      items.value = Array.isArray(res.items) ? res.items : [];
    }
  } catch (err) {
    if (seq === loadSeq) {
      app.pushError(errText(err), "loadFactLedger");
      items.value = [];
    }
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

function resetFilters(): void {
  filterScopeType.value = "";
  filterScopeId.value = "";
  filterStatus.value = "active";
  filterKeyword.value = "";
  void reload();
}

function openCreate(): void {
  editingClaim.value = null;
  editorOpen.value = true;
}

function openEdit(claim: FactClaim): void {
  editingClaim.value = claim;
  editorOpen.value = true;
}

async function onRetract(claim: FactClaim): Promise<void> {
  if (busyClaimId.value) return;
  const confirmed = await app.requestConfirmation({
    title: "撤回结构化事实",
    message: `确定撤回事实「${claim.fact_key}：${claim.value_text}」？撤回后不再参与画像构建，可随时恢复。`,
    confirmText: "确认撤回",
    danger: true,
  });
  if (!confirmed) return;

  busyClaimId.value = claim.claim_id;
  try {
    await retractFactClaim(claim.claim_id, graph.effectiveScope());
    logs.log(`事实已撤回：${claim.fact_key} (${claim.claim_id.slice(0, 10)})`, "info");
    app.pushSuccess?.("事实已撤回", "retractFact");
    await reload();
  } catch (err) {
    app.pushError(errText(err), "retractFact");
  } finally {
    busyClaimId.value = "";
  }
}

async function onRestore(claim: FactClaim): Promise<void> {
  if (busyClaimId.value) return;
  busyClaimId.value = claim.claim_id;
  try {
    await restoreFactClaim(claim.claim_id, graph.effectiveScope());
    logs.log(`事实已恢复：${claim.fact_key} (${claim.claim_id.slice(0, 10)})`, "info");
    app.pushSuccess?.("事实已恢复生效", "restoreFact");
    await reload();
  } catch (err) {
    app.pushError(errText(err), "restoreFact");
  } finally {
    busyClaimId.value = "";
  }
}

function scopeTypeText(type: string): string {
  if (type === "person") return "人物";
  if (type === "chat") return "会话";
  return type || "未知";
}

watch(
  () => graph.effectiveScope(),
  () => {
    items.value = [];
    void reload();
  },
  { immediate: true },
);

watch(
  () => memory.syncVersion,
  () => {
    void reload();
  },
);
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>结构化事实台账</h2>
        <span class="section-label tabular">
          人物与会话事实声明、否定约束与 6 大画像分区 · 当前显示 {{ filteredItems.length }} 条
        </span>
      </div>
      <div class="toolbar">
        <button class="btn" type="button" :disabled="loading" @click="reload">
          {{ loading ? "查询中…" : "刷新台账" }}
        </button>
        <button class="btn primary" type="button" @click="openCreate">新增事实</button>
      </div>
    </div>

    <div class="fact-filters">
      <label class="field" style="min-width: 140px; flex: 1">
        <span>归属类型</span>
        <select v-model="filterScopeType" class="select" @change="reload">
          <option value="">全部类型</option>
          <option value="person">人物 (person)</option>
          <option value="chat">会话 (chat)</option>
        </select>
      </label>

      <CandidateInput
        v-model="filterScopeId"
        :source="personFilterSource"
        :debounce-ms="180"
        label="归属对象 ID"
        placeholder="可选：人物 ID 或会话 ID"
        :flex="1.6"
        @choose="reload"
        @keydown.enter.prevent="reload"
      />

      <label class="field" style="min-width: 135px; flex: 1">
        <span>事实状态</span>
        <select v-model="filterStatus" class="select" @change="reload">
          <option v-for="opt in FACT_STATUS_OPTIONS" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </label>

      <label class="field" style="min-width: 160px; flex: 1.4">
        <span>关键词过滤</span>
        <input
          v-model="filterKeyword"
          class="input"
          placeholder="筛选属性键或事实内容…"
          @keyup.enter="reload"
        />
      </label>

      <div class="filter-btns">
        <button class="btn" type="button" :disabled="loading" @click="reload">
          筛选记录
        </button>
        <button class="btn" type="button" :disabled="loading" @click="resetFilters">
          重置条件
        </button>
      </div>
    </div>

    <div class="fact-list-wrap">
      <div v-if="loading && !items.length" class="empty">正在读取结构化事实台账…</div>
      <div v-else-if="!filteredItems.length" class="empty">
        {{
          items.length
            ? "没有匹配当前关键词的事实记录"
            : "当前筛选条件下暂无结构化事实，点击右上角「新增事实」录入"
        }}
      </div>

      <div v-else class="rows">
        <article v-for="claim in filteredItems" :key="claim.claim_id" class="fact-row">
          <div class="fact-main">
            <div class="fact-title-line">
              <span
                v-if="claim.polarity === 'negative'"
                class="tag bad"
                title="否定极性声明：明确排斥或否定该值"
              >
                否定事实
              </span>
              <span v-else class="tag ok">肯定事实</span>

              <h3 class="fact-kv">
                <span class="fact-key">{{ claim.fact_key }}</span>
                <span class="fact-sep">{{ claim.polarity === "negative" ? "≠" : "：" }}</span>
                <span class="fact-val">{{ claim.value_text }}</span>
              </h3>

              <span class="tag" :class="factStatusMeta(claim.status).tone">
                {{ factStatusMeta(claim.status).label }}
              </span>
            </div>

            <div class="tags tabular">
              <span class="tag">{{ scopeTypeText(claim.scope_type) }}：{{ claim.scope_id }}</span>
              <span class="tag">{{ factSectionLabel(claim.profile_section) }}</span>
              <span class="tag">{{ factStabilityLabel(claim.stability) }}</span>
              <span class="tag">{{ factCardinalityLabel(claim.cardinality) }}</span>
              <span class="tag">{{ factAuthorityLabel(claim.authority) }}</span>
              <span class="tag mono">
                置信度 {{ Number(claim.confidence ?? 1).toFixed(2) }}
              </span>
              <span v-if="claim.updated_at" class="tag">
                更新于 {{ formatTs(claim.updated_at) }}
              </span>
            </div>
          </div>

          <div class="fact-actions">
            <button
              class="btn sm"
              type="button"
              :disabled="busyClaimId === claim.claim_id"
              @click="openEdit(claim)"
            >
              编辑
            </button>
            <button
              v-if="claim.status === 'retracted'"
              class="btn sm"
              type="button"
              :disabled="busyClaimId === claim.claim_id"
              @click="onRestore(claim)"
            >
              恢复事实
            </button>
            <button
              v-else
              class="btn danger sm"
              type="button"
              :disabled="busyClaimId === claim.claim_id"
              @click="onRetract(claim)"
            >
              撤回事实
            </button>
          </div>
        </article>
      </div>
    </div>

    <FactEditorDialog
      v-model="editorOpen"
      :editing-claim="editingClaim"
      :default-scope-type="filterScopeType || 'person'"
      :default-scope-id="filterScopeId"
      @saved="reload"
    />
  </div>
</template>

<style scoped>
.fact-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--hairline);
}

.filter-btns {
  display: flex;
  gap: 8px;
}

.fact-list-wrap {
  margin-top: 4px;
}

.fact-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 0;
  flex-wrap: wrap;
}

.fact-main {
  flex: 1 1 320px;
  min-width: 0;
}

.fact-title-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.fact-kv {
  margin: 0;
  font-size: 14px;
  line-height: 1.4;
  color: var(--ink);
  word-break: break-word;
}

.fact-key {
  font-weight: 700;
}

.fact-sep {
  margin: 0 4px;
  color: var(--muted);
}

.fact-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
</style>
