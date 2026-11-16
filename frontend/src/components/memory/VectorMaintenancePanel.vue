<script setup lang="ts">
// 双池向量维护面板：展示段落向量池 (paragraph_pool) 与图谱向量池 (graph_pool) 规模及维度，
// 支持「回填缺失向量 (paragraph_backfill_once)」与二次确认「重建双池向量 (rebuild_all_vectors)」。
import { computed } from "vue";
import { useMemoryStore } from "@/stores/memory";
import { extractVectorPools, runtimeMessageText } from "@/utils/dashboardText";

const memory = useMemoryStore();

const pools = computed(() => extractVectorPools(memory.vectorReport));
const isDual = computed(() => pools.value?.effective_mode === "dual");

const paragraphCount = computed(() => {
  if (isDual.value) return Number(pools.value?.paragraph_pool?.num_vectors ?? 0);
  return Number(pools.value?.single_pool?.num_vectors ?? 0);
});

const paragraphDim = computed(() => {
  if (isDual.value) {
    return (
      pools.value?.paragraph_pool?.dimension ||
      memory.vectorReport?.detected_dimension ||
      memory.vectorReport?.dimension ||
      null
    );
  }
  return (
    pools.value?.single_pool?.dimension ||
    memory.vectorReport?.detected_dimension ||
    memory.vectorReport?.dimension ||
    null
  );
});

const graphCount = computed(() => Number(pools.value?.graph_pool?.num_vectors ?? 0));
const graphDim = computed(
  () =>
    pools.value?.graph_pool?.dimension ||
    memory.vectorReport?.detected_dimension ||
    memory.vectorReport?.dimension ||
    null,
);

const modeTag = computed(() => {
  if (!pools.value) return { text: "待检查", tone: "" };
  if (isDual.value) {
    return {
      text: pools.value.ready !== false ? "双池隔离就绪" : "双池降级",
      tone: pools.value.ready !== false ? "ok" : "warn",
    };
  }
  return { text: "单池兼容模式", tone: "warn" };
});

const statusNote = computed(() =>
  runtimeMessageText(memory.vectorReport, "点击「刷新池状态」读取双池向量诊断"),
);

const maintChips = computed(() => {
  const res = memory.vectorMaintResult;
  if (!res) return [];
  const list: string[] = [];
  if (res.processed != null) list.push(`扫描 ${res.processed} 条`);
  if (res.generated != null) list.push(`生成向量 ${res.generated} 条`);
  if (res.paragraph_generated != null) list.push(`段落池 +${res.paragraph_generated}`);
  if (res.entity_generated != null) list.push(`实体向量 +${res.entity_generated}`);
  if (res.relation_generated != null) list.push(`关系向量 +${res.relation_generated}`);
  return list;
});
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div class="vec-head">
        <h2>双池向量索引维护</h2>
        <span class="section-label">{{ statusNote }}</span>
      </div>
      <div class="toolbar">
        <span class="tag" :class="modeTag.tone">{{ modeTag.text }}</span>
        <button
          class="btn sm"
          type="button"
          :disabled="memory.loadingVectors || memory.vectorBusy"
          @click="memory.loadVectorStatus()"
        >
          {{ memory.loadingVectors ? "检测中…" : "刷新池状态" }}
        </button>
      </div>
    </div>

    <div v-if="pools?.configured_mode === 'dual' && !isDual" class="dual-upgrade-note">
      <span class="tag warn">升级提示</span>
      <span>
        当前配置为双向量池模式，但缺少双池清单而降级在单池运行。点击下方「重建双池向量」即可在线启用段落池与图谱池隔离检索。
      </span>
    </div>

    <div class="pool-cards">
      <div class="pool-card">
        <span class="pool-label">
          {{ isDual ? "段落向量池 (paragraph_pool)" : "主向量池 (single_pool)" }}
        </span>
        <strong class="pool-value tabular">{{ memory.vectorReport ? paragraphCount : "-" }}</strong>
        <span class="pool-sub tabular">
          {{ paragraphDim ? `维度 ${paragraphDim}d · 原文段落语义索引` : "等待自检读取维度" }}
        </span>
      </div>

      <div class="pool-card">
        <span class="pool-label">图谱向量池 (graph_pool)</span>
        <strong class="pool-value tabular">
          {{ memory.vectorReport ? (isDual ? graphCount : "共享单池") : "-" }}
        </strong>
        <span class="pool-sub tabular">
          {{
            isDual
              ? graphDim
                ? `维度 ${graphDim}d · 实体与三元组索引`
                : "隔离存储实体与关系向量"
              : "当前未启用独立图谱向量池"
          }}
        </span>
      </div>
    </div>

    <div class="toolbar vec-actions">
      <button
        class="btn"
        :class="{ primary: isDual }"
        type="button"
        :disabled="memory.vectorBusy"
        @click="memory.backfillMissingVectors(200)"
      >
        {{ memory.vectorBusy ? "执行中…" : "回填缺失向量" }}
      </button>
      <button
        class="btn"
        :class="pools?.configured_mode === 'dual' && !isDual ? 'primary' : 'danger'"
        type="button"
        :disabled="memory.vectorBusy"
        @click="memory.rebuildAllVectors()"
      >
        {{ memory.vectorBusy ? "正在重建…" : "重建双池向量" }}
      </button>
    </div>

    <div v-if="maintChips.length" class="tags tabular maint-chips">
      <span v-for="chip in maintChips" :key="chip" class="tag ok">{{ chip }}</span>
    </div>
  </div>
</template>

<style scoped>
.vec-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.dual-upgrade-note {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid var(--hairline);
  border-left: 3px solid var(--amber);
  border-radius: var(--radius-md);
  background: var(--surface-soft);
  color: var(--text);
  font-size: 12.5px;
  line-height: 1.5;
}

.pool-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 680px) {
  .pool-cards {
    grid-template-columns: 1fr;
  }
}

.pool-card {
  padding: 14px 16px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pool-label {
  font-size: 12px;
  color: var(--muted);
  font-weight: 600;
}

.pool-value {
  font-size: 24px;
  font-weight: 720;
  color: var(--ink);
  line-height: 1.2;
}

.pool-sub {
  font-size: 12px;
  color: var(--muted);
}

.vec-actions {
  margin-top: 12px;
}

.maint-chips {
  margin-top: 10px;
}
</style>
