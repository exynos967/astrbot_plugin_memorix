<script setup lang="ts">
// 画像查询工具栏：关键词输入（支持候选菜单 + Enter 搜索）+ TopK + 查询画像 + 强制重算 + 登记/更新人物表单。
import { computed, reactive, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { usePeopleStore } from "@/stores/people";
import { useGraphStore } from "@/stores/graph";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import { personAliases, personDisplayName } from "@/utils/personText";
import type { CandidateItem } from "@/stores/candidate";

const store = usePeopleStore();
const graph = useGraphStore();
const { keyword } = storeToRefs(store);

const kwRef = ref<HTMLInputElement | null>(null);
const showUpsertDrawer = ref(false);

const upsertForm = reactive({
  personId: "",
  personName: "",
  nickname: "",
  platform: "",
  userId: "",
  groupNickNames: "",
});

const canForceRefresh = computed(
  () => Boolean(store.overrideId.trim() || store.keyword.trim()),
);

function resetUpsertForm(): void {
  upsertForm.personId = "";
  upsertForm.personName = "";
  upsertForm.nickname = "";
  upsertForm.platform = "";
  upsertForm.userId = "";
  upsertForm.groupNickNames = "";
}

watch(
  () => graph.effectiveScope(),
  () => {
    showUpsertDrawer.value = false;
    resetUpsertForm();
  },
);

async function source(kw: string): Promise<CandidateItem[]> {
  const items = await store.suggestPersons(kw);
  const seen = new Set<string>();
  const result: CandidateItem[] = [];
  for (const item of items) {
    const pid = String(item.person_id || "").trim();
    const display = personDisplayName(item) || pid;
    if (!display) continue;
    const dedupKey = pid || display;
    if (seen.has(dedupKey)) continue;
    seen.add(dedupKey);
    const hintParts = [item.platform, pid && pid !== display ? pid : ""].filter(Boolean);
    result.push({
      value: display,
      label: display,
      hint: hintParts.join(" · "),
      kind: pid,
    });
  }
  return result;
}

const cm = useCandidateMenu({
  inputRef: kwRef,
  model: keyword,
  source,
  debounceMs: 180,
  onChoose: (item) => {
    store.keyword = item.value;
    void store.query(false, item.kind || "");
  },
});

function onKeydownEnter(e: KeyboardEvent): void {
  if (e.isComposing) return;
  setTimeout(() => {
    if (!store.querying && store.keyword.trim()) {
      void store.query(false);
    }
  }, 0);
}

function prefillFromCurrent(): void {
  const pid = store.overrideId.trim() || store.profile?.person_id || "";
  const regItem = store.candidates.find((c) => c.person_id === pid);
  upsertForm.personId = pid;
  upsertForm.personName =
    regItem?.person_name || store.profile?.person_name || store.keyword.trim() || "";
  upsertForm.nickname = regItem?.nickname || "";
  upsertForm.platform = regItem?.platform || "";
  upsertForm.userId = regItem?.user_id || "";
  const aliasList = regItem ? personAliases(regItem) : store.profile?.aliases || [];
  upsertForm.groupNickNames = aliasList.join(", ");
}

function toggleUpsertDrawer(): void {
  showUpsertDrawer.value = !showUpsertDrawer.value;
  if (showUpsertDrawer.value && !upsertForm.personId && (store.overrideId || store.profile)) {
    prefillFromCurrent();
  }
}

async function onSubmitUpsert(): Promise<void> {
  const aliases = upsertForm.groupNickNames
    .split(/[\r\n,，]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const ok = await store.upsertPerson({
    person_id: upsertForm.personId.trim(),
    person_name: upsertForm.personName.trim() || undefined,
    nickname: upsertForm.nickname.trim() || undefined,
    platform: upsertForm.platform.trim() || undefined,
    user_id: upsertForm.userId.trim() || undefined,
    group_nick_name: aliases.length ? aliases : undefined,
  });
  if (ok) {
    showUpsertDrawer.value = false;
  }
}
</script>

<template>
  <div class="person-query-wrap">
    <div class="toolbar people-query-toolbar">
      <div class="field query-kw-field">
        <label for="people-kw-input">人物关键词 / Person ID</label>
        <input
          id="people-kw-input"
          ref="kwRef"
          v-model="store.keyword"
          class="input"
          placeholder="输入姓名、昵称、别名或 person_id，按 Enter 查询"
          @focus="cm.open()"
          @input="cm.onInput()"
          @keydown.enter="onKeydownEnter"
        />
      </div>
      <div class="field query-topk-field">
        <label for="people-topk-input">证据条数上限</label>
        <input
          id="people-topk-input"
          v-model.number="store.topk"
          class="input tabular"
          type="number"
          min="1"
          max="50"
        />
      </div>
      <div class="query-actions">
        <button
          class="btn primary"
          type="button"
          :disabled="store.querying"
          @click="store.query(false)"
        >
          {{ store.querying ? "查询中…" : "查询画像" }}
        </button>
        <button
          class="btn"
          type="button"
          :disabled="store.querying || !canForceRefresh"
          title="忽略缓存快照，立即重新聚合并计算人物画像"
          @click="store.query(true, store.overrideId.trim())"
        >
          重算画像
        </button>
        <button
          class="btn"
          type="button"
          :aria-expanded="showUpsertDrawer"
          @click="toggleUpsertDrawer"
        >
          {{ showUpsertDrawer ? "收起登记表单" : "登记人物" }}
        </button>
      </div>
    </div>

    <div v-if="showUpsertDrawer" class="upsert-section">
      <div class="panel-title" style="margin-bottom: 10px">
        <h3>登记或更新人物档案</h3>
        <button
          v-if="store.overrideId || store.profile"
          class="btn sm"
          type="button"
          @click="prefillFromCurrent"
        >
          填入当前人物
        </button>
      </div>
      <div class="grid-3">
        <div class="field">
          <label for="upsert-person-id">Person ID（必填主键）</label>
          <input
            id="upsert-person-id"
            v-model="upsertForm.personId"
            class="input mono"
            placeholder="例如：qq:10001 或 person_xxx"
          />
        </div>
        <div class="field">
          <label for="upsert-person-name">展示姓名</label>
          <input
            id="upsert-person-name"
            v-model="upsertForm.personName"
            class="input"
            placeholder="例如：爱丽丝"
          />
        </div>
        <div class="field">
          <label for="upsert-nickname">常用昵称</label>
          <input
            id="upsert-nickname"
            v-model="upsertForm.nickname"
            class="input"
            placeholder="可选昵称"
          />
        </div>
        <div class="field">
          <label for="upsert-platform">来源平台</label>
          <input
            id="upsert-platform"
            v-model="upsertForm.platform"
            class="input"
            placeholder="例如：aiocqhttp / telegram"
          />
        </div>
        <div class="field">
          <label for="upsert-user-id">平台账号 ID</label>
          <input
            id="upsert-user-id"
            v-model="upsertForm.userId"
            class="input mono"
            placeholder="平台原始 user_id"
          />
        </div>
        <div class="field">
          <label for="upsert-group-nicks">附加别名（逗号分隔）</label>
          <input
            id="upsert-group-nicks"
            v-model="upsertForm.groupNickNames"
            class="input"
            placeholder="例如：小爱, Alice"
          />
        </div>
      </div>
      <div class="toolbar" style="margin-top: 10px">
        <button
          class="btn"
          type="button"
          :disabled="store.upserting || !upsertForm.personId.trim()"
          @click="onSubmitUpsert"
        >
          {{ store.upserting ? "保存中…" : "保存人物档案" }}
        </button>
        <button class="btn" type="button" @click="showUpsertDrawer = false">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.person-query-wrap {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.people-query-toolbar {
  align-items: flex-end;
  flex-wrap: wrap;
}

.query-kw-field {
  flex: 2 1 240px;
}

.query-topk-field {
  flex: 0 0 116px;
}

.query-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.upsert-section {
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
}

.upsert-section h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 680;
  color: var(--ink);
}
</style>
