<script setup lang="ts">
// 关系操作面板：输入 hash/查询 + 强化/保护/冷冻 三按钮 + 结果展示。
// 从 legacy view-memory 关系操作（index.html 行 2338-2346, 4608-4633）迁移。
// 输入本地 ref，操作上抛 run(action)，结果从 props.result 读（store 单一数据源）。
import { computed, ref } from "vue";
import type { MemoryActionResult } from "@/services/memoryApi";

const props = defineProps<{
  result: MemoryActionResult | null;
  resultId: string;
  resultAction: string;
  busy: boolean;
}>();

const emit = defineEmits<{ run: [action: "reinforce" | "protect" | "freeze", id: string] }>();

const id = ref("");

const success = computed(() => props.result?.success !== false);
const message = computed(() => {
  if (!props.result) return "";
  const map: Record<string, string> = {
    reinforce: "强化完成",
    protect: "保护完成",
    freeze: "冷冻完成",
  };
  return props.result.message || map[props.resultAction] || "操作完成";
});
const tags = computed(() => {
  if (!props.result) return [];
  return [
    success.value ? "成功" : "未匹配",
    props.result.count != null ? `${props.result.count} 条关系` : "",
    props.result.revived != null ? `复活 ${props.result.revived}` : "",
    props.result.frozen_edges != null ? `冷冻边 ${props.result.frozen_edges}` : "",
    props.result.mode ? (props.result.mode === "ttl" ? "限时保护" : "置顶保护") : "",
  ].filter(Boolean);
});

function run(action: "reinforce" | "protect" | "freeze"): void {
  emit("run", action, id.value);
}
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title"><h2>关系操作</h2></div>
    <div class="field">
      <label>关系 hash 或查询</label>
      <input v-model="id" class="input" placeholder="Alice、Alice_Bob 或关系 hash" />
    </div>
    <div class="toolbar" style="margin-top: 10px">
      <button class="btn" :disabled="busy" @click="run('reinforce')">强化</button>
      <button class="btn" :disabled="busy" @click="run('protect')">保护 24h</button>
      <button class="btn danger" :disabled="busy" @click="run('freeze')">冷冻</button>
    </div>
    <div class="result-list" style="margin-top: 12px">
      <div v-if="!result" class="empty">暂无操作</div>
      <div v-else class="result">
        <div class="result-head">
          <h3>{{ message }}</h3>
        </div>
        <p>{{ resultId }}</p>
        <div class="tags">
          <span v-for="tag in tags" :key="tag" class="tag" :class="success ? 'ok' : 'warn'">{{ tag }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
