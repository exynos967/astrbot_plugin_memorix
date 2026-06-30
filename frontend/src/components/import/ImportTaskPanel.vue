<script setup lang="ts">
// 导入任务创建表单。
// 从 legacy view-import 左列（index.html 行 2247-2259, 4386-4414）迁移。
// 模式 select / 来源 input(候选 graph.nodeLabels) / 导入内容 textarea / 高级 Options JSON(可折叠) / 创建按钮。
// payloadForImport：mode===json 或 raw 以 { [ 开头时尝试 JSON.parse，失败回退原文。
// parseOptions：JSON.parse 失败回退 {}，source 非空时写入 options.source。
import { ref } from "vue";
import { storeToRefs } from "pinia";
import { useTaskStore } from "@/stores/task";
import { useGraphStore } from "@/stores/graph";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import type { CandidateItem } from "@/stores/candidate";
import type { ImportMode } from "@/services/taskApi";

const store = useTaskStore();
const graph = useGraphStore();
const { creating } = storeToRefs(store);

// 本地表单状态
const mode = ref<ImportMode>("text");
const payload = ref("");
const optionsText = ref("{}");
const source = ref("web_import");

// 来源输入框 template ref，供候选菜单 attach
const sourceInput = ref<HTMLInputElement | null>(null);

/** graph-node 候选源：按 keyword 过滤 nodeLabels，keyword 为空返回全量。 */
function sourceCandidates(keyword: string): CandidateItem[] {
  const kw = keyword.trim().toLowerCase();
  return graph.nodeLabels
    .filter((label) => !kw || label.toLowerCase().includes(kw))
    .slice(0, 50)
    .map((value) => ({ value, kind: "节点" }));
}

const sourceMenu = useCandidateMenu({
  inputRef: sourceInput,
  model: source,
  source: sourceCandidates,
  debounceMs: 0,
});

/**
 * payload 解析：mode===json 或 raw 以 { [ 开头时尝试 JSON.parse，失败回退原文。
 * 与 legacy payloadForImport（index.html 行 4386-4393）行为一致。
 */
function payloadForImport(raw: string): unknown {
  const text = raw.trim();
  if (mode.value === "json" || text.startsWith("{") || text.startsWith("[")) {
    try {
      return JSON.parse(text);
    } catch {
      return raw;
    }
  }
  return raw;
}

/** Options JSON 解析：失败回退 {}。source 非空时写入 options.source。 */
function parseOptions(): Record<string, unknown> {
  let opts: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(optionsText.value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      opts = parsed as Record<string, unknown>;
    }
  } catch {
    opts = {};
  }
  const src = source.value.trim();
  if (src) opts.source = src;
  return opts;
}

async function onCreate(): Promise<void> {
  const parsedPayload = payloadForImport(payload.value);
  const options = parseOptions();
  await store.createImport(mode.value, parsedPayload, options);
}
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title">
      <h2>导入任务</h2>
      <span class="section-label">文本 / 段落 / 关系 / JSON / 文件</span>
    </div>
    <div class="grid-2">
      <div class="field">
        <label>模式</label>
        <select v-model="mode" class="select">
          <option value="text">文本</option>
          <option value="paragraph">段落</option>
          <option value="relation">关系</option>
          <option value="json">JSON</option>
          <option value="file">文件路径</option>
        </select>
      </div>
      <div class="field">
        <label>来源</label>
        <input
          ref="sourceInput"
          v-model="source"
          class="input"
          placeholder="web_import"
          @focus="sourceMenu.open()"
          @input="sourceMenu.onInput()"
        />
      </div>
    </div>
    <div class="field" style="margin-top: 10px">
      <label>导入内容</label>
      <textarea
        v-model="payload"
        class="textarea"
        placeholder="粘贴文本、JSON，或输入文件路径"
      ></textarea>
    </div>
    <details class="advanced-panel">
      <summary>高级选项</summary>
      <div class="field" style="margin-top: 10px">
        <label>Options JSON</label>
        <textarea v-model="optionsText" class="textarea" style="min-height: 70px"></textarea>
      </div>
    </details>
    <button class="btn primary" :disabled="creating" @click="onCreate">创建导入任务</button>
  </div>
</template>
