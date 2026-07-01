<script setup lang="ts">
// 画像结果展示：标题/来源标签/关系数/证据数/profile_text/关系列表/证据列表。
// 从 legacy view-people 画像结果（index.html 行 2293-2296）迁移为纯展示。
// 派生均走 utils/personText，profile 为 null 时显示空态。
import { computed } from "vue";
import type { PersonProfile } from "@/services/personApi";
import {
  profileEvidenceCount,
  profileRelationCount,
  profileSourceLabel,
  profileTitle,
} from "@/utils/personText";

const props = defineProps<{ profile: PersonProfile | null }>();

const title = computed(() => profileTitle(props.profile));
const sourceLabel = computed(() => profileSourceLabel(props.profile));
const relationCount = computed(() => profileRelationCount(props.profile));
const evidenceCount = computed(() => profileEvidenceCount(props.profile));
const relations = computed(() => props.profile?.relation_edges || []);
const evidences = computed(() => props.profile?.vector_evidence || []);

/** 置信度百分比展示。 */
function confidencePct(c: number | undefined): string {
  if (c == null || Number.isNaN(c)) return "-";
  return `${Math.round(c * 100)}%`;
}

/** 证据分数展示。 */
function scoreText(s: number | undefined): string {
  if (s == null || Number.isNaN(s)) return "-";
  return s.toFixed(3);
}

/** 证据内容截断至前 220 字。 */
function truncate(text: string | undefined): string {
  if (!text) return "";
  return text.length > 220 ? `${text.slice(0, 220)}…` : text;
}
</script>

<template>
  <div v-if="!profile" class="empty">输入人物关键词后查询</div>
  <div v-else class="result">
    <div class="result-head">
      <h3>{{ title }}</h3>
      <div class="tags">
        <span class="tag">{{ sourceLabel }}</span>
        <span class="tag">关系 {{ relationCount }}</span>
        <span class="tag">证据 {{ evidenceCount }}</span>
      </div>
    </div>
    <p v-if="profile.profile_text" class="profile-text">{{ profile.profile_text }}</p>
    <p v-else class="empty">无画像文本</p>

    <div v-if="relations.length" class="sub-section">
      <h4>关系</h4>
      <div class="edge-list">
        <div v-for="(edge, idx) in relations" :key="idx" class="edge-row">
          <span class="edge-subject">{{ edge.subject || "-" }}</span>
          <span class="edge-predicate">—{{ edge.predicate || "?" }}→</span>
          <span class="edge-object">{{ edge.object || "-" }}</span>
          <span class="tag mono">{{ confidencePct(edge.confidence) }}</span>
        </div>
      </div>
    </div>

    <div v-if="evidences.length" class="sub-section">
      <h4>证据</h4>
      <div class="evidence-list">
        <div v-for="(ev, idx) in evidences" :key="idx" class="evidence-row">
          <div class="tags">
            <span v-if="ev.type" class="tag">{{ ev.type }}</span>
            <span class="tag mono">{{ scoreText(ev.score) }}</span>
          </div>
          <p>{{ truncate(ev.content) }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.profile-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.sub-section {
  margin-top: 12px;
}

.sub-section h4 {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--muted);
}

.edge-list,
.evidence-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.edge-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
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
  padding: 6px 8px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  font-size: 13px;
}

.evidence-row p {
  margin: 4px 0 0;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
