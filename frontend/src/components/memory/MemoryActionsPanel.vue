<script setup lang="ts">
// 关系操作面板：支持限时保护、永久置顶（hours=0 / pin）与关系冷冻，扁平化操作反馈栏（<=2 层盒嵌套）。
import { computed, ref } from "vue";
import type { MemoryAction, MemoryActionResult } from "@/services/memoryApi";
import { formatMemoryHours } from "@/utils/memoryText";

const props = defineProps<{
  result: MemoryActionResult | null;
  resultId: string;
  resultAction: string;
  busy: boolean;
}>();

const emit = defineEmits<{
  run: [action: MemoryAction, id: string, hours: number];
}>();

const id = ref("");
const protectHours = ref<number>(24);

const isSuccess = computed(() => Boolean(props.result && props.result.success !== false));

const message = computed(() => {
  if (!props.result) return "";
  if (props.result.success === false) {
    return props.result.message || "未匹配到可操作的关系边";
  }
  const map: Record<string, string> = {
    reinforce: "关系强化已生效",
    protect: props.result.mode === "pin" ? "已设为永久置顶保护" : "关系限时保护已生效",
    freeze: "关系已转入冷冻状态",
  };
  return props.result.message || map[props.resultAction] || "操作完成";
});

const tags = computed(() => {
  if (!props.result) return [];
  return [
    isSuccess.value ? "执行成功" : "未命中目标",
    props.result.count != null ? `影响 ${props.result.count} 条关系` : "",
    props.result.revived != null && Number(props.result.revived) > 0
      ? `唤醒冷冻 ${props.result.revived} 条`
      : "",
    props.result.frozen_edges != null && Number(props.result.frozen_edges) > 0
      ? `冷冻图边 ${props.result.frozen_edges} 条`
      : "",
    props.result.mode
      ? props.result.mode === "pin"
        ? "永久置顶"
        : "限时 TTL"
      : "",
  ].filter(Boolean);
});

function run(action: MemoryAction, customHours?: number): void {
  const hrs =
    action === "protect"
      ? customHours != null
        ? customHours
        : Math.max(1, Number(protectHours.value) || 24)
      : 24;
  emit("run", action, id.value, hrs);
}

function onEnter(): void {
  if (props.busy) return;
  run("reinforce");
}
</script>

<template>
  <div class="band actions-panel">
    <div class="panel-title">
      <h2>关系记忆保护与调控</h2>
      <span class="section-label">按回车快速强化关系</span>
    </div>

    <div class="action-inputs">
      <div class="field" style="flex: 2 1 200px">
        <label for="memory-action-target">关系哈希 / 实体对 / 检索词</label>
        <input
          id="memory-action-target"
          v-model="id"
          class="input"
          placeholder="例如：爱丽丝、爱丽丝_鲍勃 或 64 位关系哈希"
          :disabled="busy"
          @keyup.enter="onEnter"
        />
      </div>

      <label class="field hours-field">
        <span>限时保护时长</span>
        <select v-model.number="protectHours" class="select tabular" :disabled="busy">
          <option :value="24">24 小时（1 天）</option>
          <option :value="72">72 小时（3 天）</option>
          <option :value="168">168 小时（7 天）</option>
          <option :value="720">720 小时（30 天）</option>
        </select>
      </label>
    </div>

    <div class="toolbar action-buttons">
      <button class="btn primary" type="button" :disabled="busy" @click="run('reinforce')">
        强化关系
      </button>
      <button class="btn" type="button" :disabled="busy" @click="run('protect')">
        保护 {{ formatMemoryHours(protectHours) }}
      </button>
      <button
        class="btn"
        type="button"
        :disabled="busy"
        title="设为永不衰减置顶保护（hours=0）"
        @click="run('protect', 0)"
      >
        设为永久置顶
      </button>
      <button class="btn danger" type="button" :disabled="busy" @click="run('freeze')">
        冷冻关系
      </button>
    </div>

    <div class="action-feedback">
      <div v-if="!result" class="section-label">输入目标标识后可执行强化、限时保护、永久置顶或冷冻。</div>
      <div v-else class="feedback-content">
        <div class="feedback-head">
          <strong>{{ message }}</strong>
          <span class="tag" :class="isSuccess ? 'ok' : 'warn'">
            {{ isSuccess ? "已生效" : "未生效" }}
          </span>
        </div>
        <p v-if="resultId" class="target-note">目标：{{ resultId }}</p>
        <div class="tags tabular">
          <span v-for="tag in tags" :key="tag" class="tag" :class="isSuccess ? 'ok' : 'warn'">
            {{ tag }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.actions-panel {
  margin-top: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.action-inputs {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
}

.hours-field {
  flex: 1 1 140px;
  min-width: 140px;
}

.action-buttons {
  gap: 8px;
}

.action-feedback {
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.feedback-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.feedback-head strong {
  font-size: 13.5px;
  color: var(--ink);
}

.target-note {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
  word-break: break-all;
}
</style>
