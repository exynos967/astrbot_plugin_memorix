<script setup lang="ts">
// 画像结果展示 + 可折叠画像证据链审查器（memory_profile_admin: evidence / correct_evidence）。
// 扁平架构：直接作为 PeopleView 主面板内部分区渲染，消除 4 层盒套盒。
import { computed, ref, watch } from "vue";
import {
  correctProfileEvidence,
  fetchProfileEvidence,
  type PersonProfile,
  type ProfileAdminEvidenceItem,
} from "@/services/personApi";
import {
  evidenceTypeLabel,
  personAliases,
  profileEvidenceCount,
  profileRelationCount,
  profileSourceLabel,
  profileTitle,
} from "@/utils/personText";
import { usePeopleStore } from "@/stores/people";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { errText } from "@/utils/error";

const props = defineProps<{ profile: PersonProfile | null }>();

const people = usePeopleStore();
const graph = useGraphStore();
const app = useAppStore();

const title = computed(() => profileTitle(props.profile));
const sourceLabel = computed(() => profileSourceLabel(props.profile));
const isManualOverride = computed(
  () => Boolean(props.profile?.has_manual_override || props.profile?.profile_source === "manual_override"),
);
const relationCount = computed(() => profileRelationCount(props.profile));
const evidenceCount = computed(() => profileEvidenceCount(props.profile));
const relations = computed(() => props.profile?.relation_edges || []);
const evidences = computed(() => props.profile?.vector_evidence || []);
const aliases = computed(() => personAliases(props.profile));

// 画像证据链折叠审查器状态
const evidenceOpen = ref(false);
const loadingChain = ref(false);
const correctingHash = ref("");
const chainItems = ref<ProfileAdminEvidenceItem[]>([]);
let chainSeq = 0;

function confidencePct(c: number | null | undefined): string {
  if (c == null || Number.isNaN(c)) return "-";
  return `${Math.round(c * 100)}%`;
}

function scoreText(s: number | null | undefined): string {
  if (s == null || Number.isNaN(s)) return "-";
  return s.toFixed(3);
}

function truncate(text: string | undefined, maxLen = 240): string {
  if (!text) return "";
  return text.length > maxLen ? `${text.slice(0, maxLen)}…` : text;
}

async function loadEvidenceChain(forceRefresh = false): Promise<void> {
  const pid = String(props.profile?.person_id || "").trim();
  const kw = String(props.profile?.person_name || "").trim();
  if (!pid && !kw) return;

  const seq = ++chainSeq;
  loadingChain.value = true;
  try {
    const res = await fetchProfileEvidence(
      {
        person_id: pid,
        person_keyword: kw,
        limit: 30,
        force_refresh: forceRefresh,
      },
      graph.effectiveScope(),
    );
    if (seq !== chainSeq) return;
    chainItems.value = res.evidence || [];
  } catch (err) {
    if (seq === chainSeq) {
      app.pushError(errText(err), "画像证据链");
    }
  } finally {
    if (seq === chainSeq) loadingChain.value = false;
  }
}

function onToggleEvidence(e: Event): void {
  const open = (e.target as HTMLDetailsElement).open;
  evidenceOpen.value = open;
  if (open && !chainItems.value.length) {
    void loadEvidenceChain(false);
  }
}

async function onCorrectEvidence(item: ProfileAdminEvidenceItem): Promise<void> {
  const pid = String(props.profile?.person_id || people.overrideId || "").trim();
  if (!pid || !item.hash) return;
  const confirmed = await app.requestConfirmation({
    title: "剔除错误画像证据",
    message: `确定从底层记忆中剔除此条${evidenceTypeLabel(item.evidence_type)}（${item.hash.slice(0, 12)}）并重算画像吗？`,
    confirmText: "剔除并重算",
    cancelText: "取消",
    danger: true,
  });
  if (!confirmed) return;

  correctingHash.value = item.evidence_key;
  try {
    const scope = graph.effectiveScope();
    await correctProfileEvidence(
      {
        person_id: pid,
        person_keyword: props.profile?.person_name || "",
        evidence_type: item.evidence_type,
        hash: item.hash,
      },
      scope,
    );
    if (scope !== graph.effectiveScope()) return;
    app.pushSuccess(`已剔除错误${evidenceTypeLabel(item.evidence_type)}并触发画像重算`, "画像证据链");
    await Promise.all([
      loadEvidenceChain(true),
      people.query(true, pid),
    ]);
  } catch (err) {
    app.pushError(errText(err), "画像证据链");
  } finally {
    correctingHash.value = "";
  }
}

watch(
  () => [props.profile?.person_id, graph.effectiveScope()],
  () => {
    chainSeq += 1;
    chainItems.value = [];
    if (evidenceOpen.value && props.profile?.person_id) {
      void loadEvidenceChain(false);
    }
  },
);
</script>

<template>
  <div v-if="!profile" class="empty">
    从左侧「人物目录」选择人物，或在上方输入关键词查询画像
  </div>
  <div v-else class="profile-section">
    <div class="profile-head">
      <div class="profile-title-group">
        <h3>{{ title }}</h3>
        <span v-if="profile.person_id" class="tag mono">{{ profile.person_id }}</span>
      </div>
      <div class="tags" style="margin-top: 0">
        <span class="tag" :class="isManualOverride ? 'warn' : 'ok'">{{ sourceLabel }}</span>
        <span v-if="profile.from_cache !== undefined" class="tag">
          {{ profile.from_cache ? "缓存快照" : "实时计算" }}
        </span>
        <span class="tag tabular">关系 {{ relationCount }}</span>
        <span class="tag tabular">证据 {{ evidenceCount }}</span>
        <span v-if="profile.uncertain_fact_count" class="tag warn tabular">
          待确认事实 {{ profile.uncertain_fact_count }}
        </span>
      </div>
    </div>

    <div v-if="aliases.length" class="tags alias-strip">
      <span class="section-label">关联别名：</span>
      <span v-for="alias in aliases" :key="alias" class="tag">{{ alias }}</span>
    </div>

    <p v-if="profile.profile_text" class="profile-text">{{ profile.profile_text }}</p>
    <p v-else class="empty">当前人物暂无生成的画像正文</p>

    <div
      v-if="isManualOverride && profile.auto_profile_text"
      class="auto-preview-box"
    >
      <div class="section-label">系统自动聚合画像参考（当前已被人工覆盖替代）：</div>
      <p class="profile-text muted-text">{{ profile.auto_profile_text }}</p>
    </div>

    <div v-if="relations.length" class="sub-section">
      <h4 class="tabular">关联知识关系（{{ relations.length }}）</h4>
      <div class="rows edge-list">
        <div v-for="(edge, idx) in relations" :key="edge.hash || idx" class="edge-row">
          <span class="edge-subject">{{ edge.subject || "-" }}</span>
          <span class="edge-predicate">—{{ edge.predicate || "?" }}→</span>
          <span class="edge-object">{{ edge.object || "-" }}</span>
          <span class="tag mono tabular">{{ confidencePct(edge.confidence) }}</span>
        </div>
      </div>
    </div>

    <div v-if="evidences.length" class="sub-section">
      <h4 class="tabular">向量召回摘要（{{ evidences.length }}）</h4>
      <div class="rows evidence-list">
        <div v-for="(ev, idx) in evidences" :key="ev.hash || idx" class="evidence-row">
          <div class="tags" style="margin-top: 0">
            <span v-if="ev.type" class="tag">{{ evidenceTypeLabel(ev.type) }}</span>
            <span v-if="ev.hash" class="tag mono">{{ String(ev.hash).slice(0, 10) }}</span>
            <span class="tag mono tabular">相关度 {{ scoreText(ev.score) }}</span>
          </div>
          <p>{{ truncate(ev.content) }}</p>
        </div>
      </div>
    </div>

    <!-- 可折叠画像证据链深度审查与纠错 -->
    <details class="advanced-panel" :open="evidenceOpen" @toggle="onToggleEvidence">
      <summary>
        展开画像证据链溯源与纠错
        <template v-if="chainItems.length">（{{ chainItems.length }} 条）</template>
      </summary>
      <div class="inspector-body">
        <div class="toolbar inspector-toolbar">
          <span class="section-label">
            核查支撑当前画像的底层段落、关系边与事实，发现脏数据时可直接剔除并重算。
          </span>
          <button
            class="btn sm"
            type="button"
            :disabled="loadingChain"
            @click="loadEvidenceChain(true)"
          >
            {{ loadingChain ? "加载中…" : "刷新证据链" }}
          </button>
        </div>

        <div v-if="loadingChain && !chainItems.length" class="empty">正在拉取画像证据链…</div>
        <div v-else-if="!chainItems.length" class="empty">暂无可追溯的底层画像证据</div>
        <div v-else class="rows evidence-list">
          <div v-for="item in chainItems" :key="item.evidence_key" class="evidence-row">
            <div class="chain-row-head">
              <div class="tags" style="margin-top: 0">
                <span class="tag">{{ evidenceTypeLabel(item.evidence_type) }}</span>
                <span v-if="item.hash" class="tag mono">{{ item.hash.slice(0, 12) }}</span>
                <span v-if="item.source" class="tag">{{ item.source }}</span>
                <span v-if="item.score != null" class="tag mono tabular">
                  得分 {{ scoreText(item.score) }}
                </span>
                <span v-if="item.confidence != null" class="tag mono tabular">
                  置信 {{ confidencePct(item.confidence) }}
                </span>
              </div>
              <button
                v-if="item.deletable && item.hash"
                class="btn sm danger"
                type="button"
                :disabled="correctingHash === item.evidence_key"
                @click="onCorrectEvidence(item)"
              >
                {{ correctingHash === item.evidence_key ? "剔除中…" : "剔除证据" }}
              </button>
              <span v-else-if="item.not_deletable_reason" class="section-label">
                {{ item.not_deletable_reason }}
              </span>
            </div>
            <p>{{ item.content }}</p>
          </div>
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
.profile-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
}

.profile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.profile-title-group {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.profile-title-group h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--ink);
}

.alias-strip {
  margin-top: 0;
  align-items: center;
}

.profile-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: var(--ink);
  line-height: 1.68;
}

.muted-text {
  color: var(--muted);
  font-size: 13px;
  margin-top: 4px;
}

.auto-preview-box {
  padding: 10px 12px;
  background: var(--surface-soft);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.sub-section {
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
}

.sub-section h4 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 680;
  color: var(--ink);
}

.edge-list,
.evidence-list {
  border-top: 1px solid var(--hairline);
}

.edge-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 8px 2px;
  font-size: 13px;
}

.edge-subject,
.edge-object {
  color: var(--text);
}

.edge-predicate {
  color: var(--accent);
}

.evidence-row {
  padding: 10px 2px;
  font-size: 13px;
}

.evidence-row p {
  margin: 6px 0 0;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.55;
}

.inspector-body {
  margin-top: 10px;
}

.inspector-toolbar {
  justify-content: space-between;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.chain-row-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
