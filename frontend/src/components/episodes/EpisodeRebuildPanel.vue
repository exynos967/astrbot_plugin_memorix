<script setup lang="ts">
// Episode 租约安全重建与队列管理面板：扁平 1px 细线布局，展示租约队列状态与分组/情景缓存复用统计。
import { computed } from "vue";
import { storeToRefs } from "pinia";
import { useEpisodeStore } from "@/stores/episode";
import CandidateInput from "@/components/common/CandidateInput.vue";
import type { CandidateItem } from "@/stores/candidate";
import { deriveRebuildMetrics } from "@/utils/episodeText";

const store = useEpisodeStore();
const { rebuildSource, rebuildResult, leaseStatus, loadingStatus, rebuilding, queueBusy } =
  storeToRefs(store);

const busy = computed(() => rebuilding.value || queueBusy.value);

async function realSourceCandidates(kw: string): Promise<CandidateItem[]> {
  const list = await store.getSourceCandidates(kw);
  return list.map((value) => ({ value, kind: "来源" }));
}

const queueCounts = computed(() => {
  const c = leaseStatus.value?.counts || {};
  return {
    pending: Number(c.pending ?? 0),
    running: Number(c.running ?? 0),
    failed: Number(c.failed ?? 0),
    done: Number(c.succeeded ?? c.done ?? 0),
  };
});

const failedSources = computed(() =>
  (leaseStatus.value?.failed || []).slice(0, 5).map((item) => ({
    source: item.source || "未知来源",
    error: item.last_error || item.error || "生成失败",
    attempts: item.attempts ?? 1,
  })),
);

const metrics = computed(() => deriveRebuildMetrics(rebuildResult.value));
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>情景租约队列与重建</h2>
        <span class="section-label">事件分组缓存复用，防止并发重复生成</span>
      </div>
      <button
        class="btn sm"
        type="button"
        :disabled="loadingStatus || busy"
        @click="store.loadLeaseStatus()"
      >
        {{ loadingStatus ? "刷新中…" : "刷新队列" }}
      </button>
    </div>

    <div class="lease-chips tabular">
      <span class="tag" :class="queueCounts.pending > 0 ? 'warn' : ''">
        待处理来源：{{ queueCounts.pending }}
      </span>
      <span class="tag" :class="queueCounts.running > 0 ? 'warn' : 'ok'">
        运行中租约：{{ queueCounts.running }}
      </span>
      <span class="tag" :class="queueCounts.failed > 0 ? 'bad' : ''">
        失败来源：{{ queueCounts.failed }}
      </span>
      <span v-if="queueCounts.done > 0" class="tag ok">已完成：{{ queueCounts.done }}</span>
      <span v-if="leaseStatus?.has_pending_paragraphs" class="tag warn">
        存在待聚类段落队列
      </span>
    </div>

    <div v-if="failedSources.length" class="failed-section">
      <div class="failed-title">最近失败来源</div>
      <div v-for="f in failedSources" :key="f.source" class="failed-item">
        <strong>{{ f.source }}</strong>
        <span class="cell-note tabular">重试 {{ f.attempts }} 次 · {{ f.error }}</span>
      </div>
    </div>

    <div class="rebuild-form">
      <CandidateInput
        v-model="rebuildSource"
        :source="realSourceCandidates"
        :debounce-ms="150"
        label="目标来源"
        placeholder="选择或输入要重建的来源名称"
        :flex="2"
        @keydown.enter.prevent="store.rebuild(false)"
      />
    </div>

    <div class="toolbar rebuild-btns">
      <button
        class="btn primary"
        type="button"
        :disabled="busy"
        @click="store.rebuild(false)"
      >
        {{ rebuilding ? "重建中…" : "重建指定来源" }}
      </button>
      <button class="btn" type="button" :disabled="busy" @click="store.rebuild(true)">
        全量重建
      </button>
      <button class="btn" type="button" :disabled="busy" @click="store.processPending(10)">
        {{ queueBusy ? "推进中…" : "推进队列" }}
      </button>
      <button
        class="btn danger"
        type="button"
        :disabled="busy"
        title="丢弃历史版本升级遗留的回填任务队列"
        @click="store.discardMigrationBackfill()"
      >
        清理迁移积压
      </button>
    </div>

    <div v-if="rebuildResult" class="rebuild-result">
      <span class="section-label">最近执行统计</span>
      <div class="tags tabular">
        <span
          v-for="chip in metrics.chips"
          :key="chip.label"
          class="tag"
          :class="chip.tone || ''"
        >
          {{ chip.label }}
        </span>
      </div>
      <div v-if="metrics.failures.length" class="failure-list">
        <p v-for="fail in metrics.failures" :key="fail.source" class="fail-line">
          {{ fail.source }}：{{ fail.error }}
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lease-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.failed-section {
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--hairline);
  background: var(--red-soft);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.failed-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--red);
}

.failed-item {
  display: flex;
  flex-direction: column;
  font-size: 12px;
}

.rebuild-form {
  display: flex;
  gap: 10px;
}

.rebuild-btns {
  margin-top: 10px;
  gap: 8px;
}

.rebuild-result {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.failure-list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.fail-line {
  margin: 0;
  font-size: 12px;
  color: var(--red);
}
</style>
