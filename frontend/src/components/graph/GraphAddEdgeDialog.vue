<template>
  <!-- 新增关系弹窗：浮层 + v-if 控制 -->
  <div v-if="modelValue" class="dialog-overlay" @click.self="close">
    <div class="dialog">
      <div class="panel-title"><h2>新增关系</h2></div>

      <div class="field">
        <CandidateInput
          v-model="source"
          :source="graphNodeSource"
          :debounce-ms="0"
          label="主体"
          placeholder="起始实体"
        />
      </div>

      <div class="field">
        <CandidateInput
          v-model="target"
          :source="graphNodeSource"
          :debounce-ms="0"
          label="客体"
          placeholder="目标实体"
        />
      </div>

      <div class="field">
        <CandidateInput
          v-model="predicate"
          :source="predicateSource"
          :debounce-ms="0"
          label="谓词"
        />
      </div>

      <div class="field">
        <label>权重</label>
        <input
          class="input"
          type="number"
          min="0.1"
          step="0.1"
          v-model.number="weight"
        />
      </div>

      <div class="toolbar">
        <button class="btn primary" @click="submit">创建</button>
        <button class="btn" @click="close">取消</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import CandidateInput from "@/components/common/CandidateInput.vue";
import type { CandidateItem } from "@/stores/candidate";

const props = defineProps<{ modelValue: boolean; defaultSource?: string }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const store = useGraphStore();
const app = useAppStore();

// 表单字段
const source = ref("");
const target = ref("");
const predicate = ref("关联");
const weight = ref(1);

// 候选源：实体标签
function graphNodeSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  return store.nodeLabels
    .filter((v) => !q || v.toLowerCase().includes(q))
    .slice(0, 10)
    .map((v) => ({ value: v, kind: "实体" }));
}

// 候选源：谓词标签
function predicateSource(kw: string): CandidateItem[] {
  const q = kw.trim().toLowerCase();
  return store.predicateLabels
    .filter((v) => !q || v.toLowerCase().includes(q))
    .slice(0, 10)
    .map((v) => ({ value: v, kind: "关系" }));
}

// 关闭弹窗
function close(): void {
  emit("update:modelValue", false);
}

// 打开时重置字段
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    source.value = props.defaultSource || "";
    target.value = "";
    predicate.value = "关联";
    weight.value = 1;
  },
);

// 创建关系
async function submit(): Promise<void> {
  const s = source.value.trim();
  const t = target.value.trim();
  if (!s || !t) {
    app.pushError("请输入主体与客体", "addEdge");
    return;
  }
  const ok = await store.addEdge(s, t, predicate.value.trim(), weight.value);
  if (!ok) return;
  await store.loadGraph();
  close();
}
</script>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.dialog {
  background: var(--panel-bg, #fff);
  border-radius: 10px;
  padding: 18px 20px;
  width: min(420px, 92vw);
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dialog .toolbar {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
</style>
