<script setup lang="ts">
// 新增关系弹窗：Teleport 到 body，z-index 9000 保证 CandidateMenu (9200) 正常浮出，零硬编码色值。
import { onBeforeUnmount, ref, watch } from "vue";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import CandidateInput from "@/components/common/CandidateInput.vue";
import type { CandidateItem } from "@/stores/candidate";

const props = defineProps<{ modelValue: boolean; defaultSource?: string }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const store = useGraphStore();
const app = useAppStore();

const source = ref("");
const target = ref("");
const predicate = ref("关联");
const weight = ref(1);
const submitting = ref(false);

function graphNodeSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  return store.nodeLabels
    .filter((v) => !q || v.toLowerCase().includes(q))
    .slice(0, 24)
    .map((v) => ({ value: v, kind: "实体" }));
}

function predicateSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  return store.predicateLabels
    .filter((v) => !q || v.toLowerCase().includes(q))
    .slice(0, 24)
    .map((v) => ({ value: v, kind: "关系" }));
}

function close(): void {
  if (submitting.value) return;
  emit("update:modelValue", false);
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape" && !app.confirmation) {
    e.preventDefault();
    close();
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      source.value = props.defaultSource || "";
      target.value = "";
      predicate.value = "关联";
      weight.value = 1;
      submitting.value = false;
      window.addEventListener("keydown", onKeydown);
    } else {
      window.removeEventListener("keydown", onKeydown);
    }
  },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});

async function submit(): Promise<void> {
  if (submitting.value) return;
  const s = source.value.trim();
  const t = target.value.trim();
  if (!s || !t) {
    app.pushError("请填写主体实体与客体实体", "addEdge");
    return;
  }
  const w = Number(weight.value);
  if (!Number.isFinite(w) || w <= 0) {
    app.pushError("关系权重须为大于 0 的数值", "addEdge");
    return;
  }
  submitting.value = true;
  try {
    const ok = await store.addEdge(s, t, predicate.value.trim() || "关联", w);
    if (!ok) return;
    app.pushSuccess?.(`关系「${s} → ${t}」已创建`, "addEdge");
    await store.loadGraph();
    emit("update:modelValue", false);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="dialog-overlay" @click.self="close">
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="add-edge-title">
        <div class="panel-title">
          <h2 id="add-edge-title">新增记忆关系</h2>
          <button
            class="btn ghost sm"
            type="button"
            aria-label="关闭弹窗"
            :disabled="submitting"
            @click="close"
          >
            关闭
          </button>
        </div>

        <CandidateInput
          v-model="source"
          :source="graphNodeSource"
          :debounce-ms="80"
          label="主体实体（起点）"
          placeholder="输入或选择起始实体"
        />

        <CandidateInput
          v-model="target"
          :source="graphNodeSource"
          :debounce-ms="80"
          label="客体实体（终点）"
          placeholder="输入或选择目标实体"
        />

        <CandidateInput
          v-model="predicate"
          :source="predicateSource"
          :debounce-ms="80"
          label="关系谓词"
          placeholder="例如：喜欢 / 负责 / 属于"
        />

        <div class="field">
          <label for="graph-add-edge-weight">初始权重</label>
          <input
            id="graph-add-edge-weight"
            v-model.number="weight"
            class="input tabular"
            type="number"
            min="0.05"
            step="0.05"
            :disabled="submitting"
            @keyup.enter="submit"
          />
        </div>

        <div class="dialog-actions">
          <button class="btn" type="button" :disabled="submitting" @click="close">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submit">
            {{ submitting ? "创建中…" : "创建关系" }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: var(--bg-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9000;
  padding: 16px;
}

.dialog {
  width: min(440px, 100%);
  padding: 20px 22px;
  background: var(--bg-elevated);
  color: var(--text);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dialog-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 4px;
}
</style>
