<script setup lang="ts">
// 已登记人物目录面板：对接 GET /v1/person/registry/list，支持关键词筛选、分页浏览与一键加载画像。
import { computed, ref, watch } from "vue";
import { usePeopleStore } from "@/stores/people";
import type { PersonRegistryItem } from "@/services/personApi";
import { personAliases, personDisplayName } from "@/utils/personText";

const props = defineProps<{
  items?: PersonRegistryItem[];
}>();

const emit = defineEmits<{
  pick: [payload: { personId: string; keyword: string }];
}>();

const store = usePeopleStore();
const filterInput = ref(store.registryKeyword);

watch(
  () => store.registryKeyword,
  (val) => {
    if (val !== filterInput.value) filterInput.value = val;
  },
);

const listItems = computed(() => props.items ?? store.candidates);
const totalCount = computed(() =>
  props.items ? props.items.length : store.registryTotal || listItems.value.length,
);
const totalPages = computed(() =>
  Math.max(1, Math.ceil((store.registryTotal || 0) / store.registryPageSize)),
);

function onSearch(): void {
  void store.loadRegistry(1, filterInput.value);
}

function onClearSearch(): void {
  filterInput.value = "";
  void store.loadRegistry(1, "");
}

function onPageChange(delta: number): void {
  const next = store.registryPage + delta;
  if (next < 1 || next > totalPages.value) return;
  void store.loadRegistry(next, filterInput.value);
}

function onSelect(item: PersonRegistryItem): void {
  const payload = {
    personId: item.person_id,
    keyword: personDisplayName(item) || item.person_id,
  };
  emit("pick", payload);
  void store.selectPerson(payload);
}
</script>

<template>
  <aside class="band person-directory">
    <div class="panel-title">
      <h2>人物目录</h2>
      <div class="tags" style="margin-top: 0">
        <span class="tag tabular">共 {{ totalCount }} 人</span>
        <button
          class="btn sm"
          type="button"
          :disabled="store.loadingCandidates"
          @click="store.refreshCandidates()"
        >
          {{ store.loadingCandidates ? "加载中…" : "刷新列表" }}
        </button>
      </div>
    </div>

    <div class="directory-search">
      <input
        v-model="filterInput"
        class="input"
        type="search"
        placeholder="筛选姓名、昵称或 person_id…"
        aria-label="筛选人物目录"
        @keydown.enter.prevent="onSearch"
      />
      <button class="btn sm" type="button" :disabled="store.loadingCandidates" @click="onSearch">
        筛选
      </button>
      <button
        v-if="filterInput.trim()"
        class="btn sm"
        type="button"
        :disabled="store.loadingCandidates"
        @click="onClearSearch"
      >
        清空
      </button>
    </div>

    <div v-if="store.loadingCandidates && !listItems.length" class="empty">
      正在读取人物目录…
    </div>
    <div v-else-if="!listItems.length" class="empty">
      暂无匹配人物，可通过右侧「登记人物」创建
    </div>
    <div v-else class="directory-list">
      <button
        v-for="item in listItems"
        :key="item.person_id"
        type="button"
        class="directory-row"
        :class="{ active: item.person_id === store.overrideId }"
        @click="onSelect(item)"
      >
        <div class="directory-item-head">
          <strong class="directory-name">{{ personDisplayName(item) }}</strong>
          <div class="tags" style="margin-top: 0">
            <span v-if="item.has_override" class="tag warn">人工覆盖</span>
            <span v-else-if="item.has_snapshot" class="tag ok">自动画像</span>
            <span v-if="item.platform" class="tag">{{ item.platform }}</span>
          </div>
        </div>
        <div class="directory-id mono">{{ item.person_id }}</div>
        <div v-if="personAliases(item).length" class="tags directory-aliases">
          <span
            v-for="alias in personAliases(item).slice(0, 5)"
            :key="alias"
            class="tag"
          >
            {{ alias }}
          </span>
        </div>
      </button>
    </div>

    <div v-if="!props.items && totalPages > 1" class="directory-pager">
      <button
        class="btn sm"
        type="button"
        :disabled="store.loadingCandidates || store.registryPage <= 1"
        @click="onPageChange(-1)"
      >
        上一页
      </button>
      <span class="pager-info mono tabular">{{ store.registryPage }} / {{ totalPages }}</span>
      <button
        class="btn sm"
        type="button"
        :disabled="store.loadingCandidates || store.registryPage >= totalPages"
        @click="onPageChange(1)"
      >
        下一页
      </button>
    </div>
  </aside>
</template>

<style scoped>
.person-directory {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 0;
}

.directory-search {
  display: flex;
  gap: 6px;
  align-items: center;
}

.directory-search .input {
  flex: 1;
  min-width: 0;
}

.directory-list {
  display: flex;
  flex-direction: column;
  max-height: 680px;
  overflow-y: auto;
  border-top: 1px solid var(--hairline);
}

.directory-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  text-align: left;
  padding: 10px 8px 10px 10px;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--hairline);
  border-left: 2px solid transparent;
  color: var(--text);
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.directory-row:hover {
  background: var(--nav-soft);
}

.directory-row.active {
  border-left-color: var(--accent);
  background: var(--accent-soft);
}

.directory-item-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 6px;
}

.directory-name {
  font-size: 13px;
  font-weight: 650;
  color: var(--ink);
  word-break: break-word;
}

.directory-id {
  font-size: 11.5px;
  color: var(--muted);
  word-break: break-all;
}

.directory-aliases {
  margin-top: 2px;
}

.directory-pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 6px;
  border-top: 1px solid var(--hairline);
}

.pager-info {
  font-size: 12px;
  color: var(--muted);
}
</style>
