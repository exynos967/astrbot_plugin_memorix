<script setup lang="ts">
// 摘要任务创建表单。
// 从 legacy view-import 右列（index.html 行 2269-2276, 4416-4429）迁移。
// Session ID / Source(候选 graph.nodeLabels) / Messages JSON / 创建按钮 / 摘要结果展示。
// parseMessages：JSON.parse 失败回退 []，与 legacy parseJsonInput(messages, []) 一致。
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import { useTaskStore } from "@/stores/task";
import { useGraphStore } from "@/stores/graph";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import type { CandidateItem } from "@/stores/candidate";

const store = useTaskStore();
const graph = useGraphStore();
const { creating, summaryResult } = storeToRefs(store);

// 本地表单状态
const sessionId = ref("");
const source = ref("web_summary");
const messagesText = ref("[]");

// Source 输入框 template ref，供候选菜单 attach
const sourceInput = ref<HTMLInputElement | null>(null);

/** graph-node 候选源：按 keyword 过滤 nodeLabels，keyword 为空返回全量。 */
function sourceCandidates(keyword: string): CandidateItem[] {
  const kw = keyword.trim().toLowerCase();
  return graph.nodeLabels
    .filter((label) => !kw || label.toLowerCase().includes(kw))
    .map((value) => ({ value, kind: "节点" }));
}

const sourceMenu = useCandidateMenu({
  inputRef: sourceInput,
  model: source,
  source: sourceCandidates,
  debounceMs: 0,
});

/** Messages JSON 解析：失败回退 []。 */
function parseMessages(): unknown[] {
  try {
    const parsed = JSON.parse(messagesText.value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

async function onCreate(): Promise<void> {
  const messages = parseMessages();
  await store.createSummary(sessionId.value, source.value, messages, 50);
}

/** 摘要结果 JSON 格式化展示。 */
const summaryJson = computed(() =>
  summaryResult.value ? JSON.stringify(summaryResult.value, null, 2) : "",
);
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title">
      <h2>摘要任务</h2>
      <span class="section-label">从会话消息生成摘要与关系</span>
    </div>
    <div class="field">
      <label>Session ID</label>
      <input v-model="sessionId" class="input" placeholder="session" />
    </div>
    <div class="field" style="margin-top: 10px">
      <label>Source</label>
      <input
        ref="sourceInput"
        v-model="source"
        class="input"
        placeholder="web_summary"
        @focus="sourceMenu.open()"
        @input="sourceMenu.onInput()"
      />
    </div>
    <div class="field" style="margin-top: 10px">
      <label>Messages JSON</label>
      <textarea
        v-model="messagesText"
        class="textarea"
        placeholder='[{"role":"user","content":"..."}]'
      ></textarea>
    </div>
    <button class="btn primary" :disabled="creating" @click="onCreate">创建摘要任务</button>

    <div v-if="!summaryResult" class="empty" style="margin-top: 12px">等待操作</div>
    <pre v-else class="json" style="margin-top: 12px">{{ summaryJson }}</pre>
  </div>
</template>
