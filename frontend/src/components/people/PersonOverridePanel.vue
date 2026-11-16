<script setup lang="ts">
// 人工覆盖表单：Person ID 候选菜单强制只回填规范 person_id（绝不混入 display_name），清除前需二次确认。
import { ref } from "vue";
import { storeToRefs } from "pinia";
import { usePeopleStore } from "@/stores/people";
import { useAppStore } from "@/stores/app";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import { personIdCandidateItems } from "@/utils/personText";
import type { CandidateItem } from "@/stores/candidate";

const store = usePeopleStore();
const app = useAppStore();
const { overrideId } = storeToRefs(store);

const idRef = ref<HTMLInputElement | null>(null);

async function source(kw: string): Promise<CandidateItem[]> {
  const items = await store.suggestPersons(kw);
  return personIdCandidateItems(items);
}

const cm = useCandidateMenu({
  inputRef: idRef,
  model: overrideId,
  source,
  debounceMs: 180,
  onChoose: (item) => {
    store.overrideId = item.value;
  },
});

async function onClearOverride(): Promise<void> {
  const id = store.overrideId.trim();
  if (!id) {
    app.pushError("请先选定要清除覆盖的 Person ID", "人工覆盖");
    return;
  }
  const confirmed = await app.requestConfirmation({
    title: "清除人工画像覆盖",
    message: `确定清除人物「${id}」的人工覆盖文本，并恢复为系统自动聚合画像吗？`,
    confirmText: "清除覆盖",
    cancelText: "取消",
    danger: true,
  });
  if (!confirmed) return;
  await store.clearOverride();
}
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>人工画像覆盖</h2>
        <span class="section-label">针对规范 <code class="mono">person_id</code> 指定优先注入文本，清除后立即恢复自动画像</span>
      </div>
      <span v-if="store.profile?.has_manual_override" class="tag warn">人工覆盖生效中</span>
    </div>
    <div class="field">
      <label for="override-person-id">Person ID（规范主键）</label>
      <input
        id="override-person-id"
        ref="idRef"
        v-model="store.overrideId"
        class="input mono"
        placeholder="从下拉选择或输入规范 person_id"
        @focus="cm.open()"
        @input="cm.onInput()"
      />
    </div>
    <div class="field" style="margin-top: 10px">
      <label for="override-text-input">覆盖画像正文</label>
      <textarea
        id="override-text-input"
        v-model="store.overrideText"
        class="textarea"
        rows="4"
        placeholder="输入替代自动画像注入上下文的人物设定描述…"
      ></textarea>
    </div>
    <div class="toolbar" style="margin-top: 10px">
      <button
        class="btn primary"
        type="button"
        :disabled="store.busy || !store.overrideId.trim() || !store.overrideText.trim()"
        @click="store.saveOverride()"
      >
        {{ store.busy ? "保存中…" : "保存人工覆盖" }}
      </button>
      <button
        class="btn danger"
        type="button"
        :disabled="store.busy || !store.overrideId.trim()"
        @click="onClearOverride"
      >
        清除覆盖
      </button>
    </div>
  </div>
</template>
