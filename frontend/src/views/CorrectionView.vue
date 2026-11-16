<script setup lang="ts">
// 纠错与调优主视图（/correction）：统一承载模糊修正、反馈审计与检索调优三大治理工作流。
import { onBeforeUnmount, watch } from "vue";
import { useRoute } from "vue-router";
import FeedbackAuditTab from "@/components/correction/FeedbackAuditTab.vue";
import FuzzyModifyTab from "@/components/correction/FuzzyModifyTab.vue";
import RetrievalTuningTab from "@/components/correction/RetrievalTuningTab.vue";
import { useGovernanceStore, type CorrectionSubTab } from "@/stores/governance";

const route = useRoute();
const store = useGovernanceStore();

watch(
  () => route.query.tab,
  (tab) => {
    const val = Array.isArray(tab) ? tab[0] : tab;
    if (val === "fuzzy" || val === "feedback" || val === "tuning") {
      store.activeTab = val as CorrectionSubTab;
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  store.stopTuningPoll();
});
</script>

<template>
  <section class="correction-view">
    <div class="correction-header">
      <div class="subtabs" role="tablist" aria-label="纠错与调优子模块">
        <button
          type="button"
          role="tab"
          class="subtab-btn"
          :class="{ active: store.activeTab === 'fuzzy' }"
          :aria-selected="store.activeTab === 'fuzzy'"
          @click="store.activeTab = 'fuzzy'"
        >
          模糊修正
        </button>
        <button
          type="button"
          role="tab"
          class="subtab-btn"
          :class="{ active: store.activeTab === 'feedback' }"
          :aria-selected="store.activeTab === 'feedback'"
          @click="store.activeTab = 'feedback'"
        >
          反馈审计
        </button>
        <button
          type="button"
          role="tab"
          class="subtab-btn"
          :class="{ active: store.activeTab === 'tuning' }"
          :aria-selected="store.activeTab === 'tuning'"
          @click="store.activeTab = 'tuning'"
        >
          检索调优
        </button>
      </div>
    </div>

    <FuzzyModifyTab v-if="store.activeTab === 'fuzzy'" />
    <FeedbackAuditTab v-else-if="store.activeTab === 'feedback'" />
    <RetrievalTuningTab v-else />
  </section>
</template>

<style scoped>
.correction-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.correction-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
