<template>
  <!-- 新增节点弹窗 -->
  <div v-if="modelValue" class="dialog-overlay" @click.self="close">
    <div class="dialog">
      <h2 class="panel-title">新增节点</h2>
      <div class="field">
        <label>实体名称</label>
        <input
          ref="nameInput"
          v-model="nodeName"
          class="input"
          type="text"
          placeholder="输入新实体名称"
          required
          @keyup.enter="submit"
        />
      </div>
      <div class="toolbar" style="margin-top: 10px">
        <button class="btn primary" @click="submit">创建</button>
        <button class="btn" @click="close">取消</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from "vue";
import { useGraphStore } from "@/stores/graph";

// 弹窗显隐：v-model 双向绑定
const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const store = useGraphStore();

// 本地节点名输入
const nodeName = ref("");
const nameInput = ref<HTMLInputElement | null>(null);

// 打开时聚焦输入框，关闭时清空
watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      nodeName.value = "";
      await nextTick();
      nameInput.value?.focus();
    } else {
      nodeName.value = "";
    }
  }
);

// 提交创建节点
async function submit() {
  const name = nodeName.value.trim();
  if (!name) return;
  const ok = await store.addNode(name);
  if (ok) {
    await store.loadGraph();
    emit("update:modelValue", false);
  }
}

// 关闭弹窗
function close() {
  emit("update:modelValue", false);
}
</script>

<style scoped>
/* 遮罩：全屏半透明，内容居中 */
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

/* 弹窗卡片 */
.dialog {
  padding: 16px 20px;
  min-width: 320px;
  max-width: 90vw;
  background: var(--card-bg, #fff);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
}
</style>
