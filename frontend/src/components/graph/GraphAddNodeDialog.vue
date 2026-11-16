<script setup lang="ts">
// 新增实体节点弹窗：Teleport 到 body，语义 Token 驱动（零硬编码色值），支持防连点锁与 Esc 关闭。
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const store = useGraphStore();
const app = useAppStore();

const nodeName = ref("");
const nodeLabel = ref("");
const submitting = ref(false);
const nameInput = ref<HTMLInputElement | null>(null);

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
  async (open) => {
    if (open) {
      nodeName.value = "";
      nodeLabel.value = "";
      submitting.value = false;
      window.addEventListener("keydown", onKeydown);
      await nextTick();
      nameInput.value?.focus();
    } else {
      window.removeEventListener("keydown", onKeydown);
      nodeName.value = "";
      nodeLabel.value = "";
    }
  },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});

async function submit(): Promise<void> {
  if (submitting.value) return;
  const name = nodeName.value.trim();
  if (!name) {
    app.pushError("请输入实体标识", "addNode");
    return;
  }
  submitting.value = true;
  try {
    const cleanLabel = nodeLabel.value.trim() || undefined;
    const ok = await store.addNode(name, cleanLabel);
    if (ok) {
      app.pushSuccess?.(`实体「${cleanLabel || name}」已创建`, "addNode");
      await store.loadGraph();
      store.selectedNode = name;
      store.selectedEdgeId = "";
      emit("update:modelValue", false);
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="dialog-overlay" @click.self="close">
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="add-node-title">
        <div class="panel-title">
          <h2 id="add-node-title">新增图谱实体</h2>
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

        <div class="field">
          <label for="graph-add-node-name">实体标识（必填）</label>
          <input
            id="graph-add-node-name"
            ref="nameInput"
            v-model="nodeName"
            class="input"
            type="text"
            placeholder="例如：爱丽丝 / Python"
            :disabled="submitting"
            @keyup.enter="submit"
          />
        </div>

        <div class="field">
          <label for="graph-add-node-label">显示标签（可选，默认同标识）</label>
          <input
            id="graph-add-node-label"
            v-model="nodeLabel"
            class="input"
            type="text"
            placeholder="可选展示名称"
            :disabled="submitting"
            @keyup.enter="submit"
          />
        </div>

        <div class="dialog-actions">
          <button class="btn" type="button" :disabled="submitting" @click="close">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submit">
            {{ submitting ? "创建中…" : "创建实体" }}
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
  width: min(420px, 100%);
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
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
</style>
