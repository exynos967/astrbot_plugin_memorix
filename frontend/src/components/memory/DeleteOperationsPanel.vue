<script setup lang="ts">
// 可恢复删除工作台与审计记录：扁平 1px 细线分区（<=2 层盒嵌套），支持删除预览、执行可恢复删除、整单恢复与清理超期归档。
import { computed, ref, watch } from "vue";
import {
  memoryAdmin,
  type DeleteExecuteResult,
  type DeleteOperation,
  type DeleteOperations,
  type DeletePreviewResult,
  type DeletePurgeResult,
} from "@/services/memoryAdminApi";
import { useGraphStore } from "@/stores/graph";
import { useMemoryStore } from "@/stores/memory";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import { deleteModeLabel, deleteStatusMeta } from "@/utils/memoryText";
import { formatTs } from "@/utils/time";
import { errText } from "@/utils/error";

const graph = useGraphStore();
const memory = useMemoryStore();
const app = useAppStore();
const logs = useLogsStore();

const items = ref<DeleteOperation[]>([]);
const loading = ref(false);
const busy = ref(false);
const message = ref("");

const deleteMode = ref<"paragraph" | "entity" | "relation" | "source" | "clear">("paragraph");
const selectorInput = ref("");
const previewResult = ref<DeletePreviewResult | null>(null);
const purgeHours = ref<number>(168);

const selectorPlaceholder = computed(() => {
  switch (deleteMode.value) {
    case "paragraph":
      return "输入 64 位段落哈希或精确段落内容片段";
    case "entity":
      return "输入要删除的图谱实体名称";
    case "relation":
      return "输入 主体|谓词|客体（如 爱丽丝|喜欢|咖啡）或关系哈希";
    case "source":
      return "输入来源名称（多个来源用英文逗号分隔）";
    case "clear":
      return "无需输入选择器（清空当前作用域全部记忆，支持整单恢复）";
  }
});

const previewChips = computed(() => {
  const counts = previewResult.value?.counts;
  if (!counts) return [];
  return [
    counts.paragraph != null ? `命中段落 ${counts.paragraph}` : "",
    counts.entity != null ? `命中实体 ${counts.entity}` : "",
    counts.relation != null ? `影响关系 ${counts.relation}` : "",
    counts.episode != null ? `关联情景 ${counts.episode}` : "",
  ].filter(Boolean);
});

function buildSelectorPayload(): Record<string, unknown> | null {
  const q = selectorInput.value.trim();
  if (deleteMode.value === "clear") return {};
  if (!q) {
    app.pushError("请先填写要预览或删除的目标条件", "deleteAdmin");
    return null;
  }
  if (deleteMode.value === "source") {
    return { source: q, sources: q };
  }
  if (deleteMode.value === "entity") {
    return { entity_name: q, query: q };
  }
  return { query: q };
}

let revision = 0;
async function reload(): Promise<void> {
  const seq = ++revision;
  loading.value = true;
  try {
    const result = await memoryAdmin<DeleteOperations>(
      "memory/delete-admin",
      "list_operations",
      { limit: 50 },
      graph.effectiveScope(),
    );
    if (seq === revision) {
      items.value = result.items || [];
    }
  } catch (error) {
    if (seq === revision) app.pushError(errText(error), "deleteOperations");
  } finally {
    if (seq === revision) loading.value = false;
  }
}

async function runPreview(): Promise<void> {
  if (busy.value) return;
  const selector = buildSelectorPayload();
  if (!selector) return;
  busy.value = true;
  try {
    const res = await memoryAdmin<DeletePreviewResult>(
      "memory/delete-admin",
      "preview",
      { mode: deleteMode.value, selector },
      graph.effectiveScope(),
    );
    previewResult.value = res;
    message.value = `已完成「${deleteModeLabel(deleteMode.value)}」删除预览（未修改存储）。`;
  } catch (error) {
    previewResult.value = null;
    app.pushError(errText(error), "deletePreview");
  } finally {
    busy.value = false;
  }
}

async function runExecute(): Promise<void> {
  if (busy.value) return;
  const selector = buildSelectorPayload();
  if (!selector) return;

  const modeName = deleteModeLabel(deleteMode.value);
  const confirmed = await app.requestConfirmation({
    title: `执行可恢复删除（${modeName}）`,
    message:
      deleteMode.value === "clear"
        ? "确定清空当前作用域下的全部记忆？该操作会生成可恢复审计单，在清理归档前可随时一键还原。"
        : `确定按「${modeName}」删除「${selectorInput.value.trim()}」？删除后可在下方审计列表或回收站恢复。`,
    confirmText: "确认执行删除",
    danger: true,
  });
  if (!confirmed) return;

  busy.value = true;
  const scope = graph.effectiveScope();
  try {
    const res = await memoryAdmin<DeleteExecuteResult>(
      "memory/delete-admin",
      "execute",
      { mode: deleteMode.value, selector },
      scope,
    );
    if (scope !== graph.effectiveScope()) return;
    const p = res.deleted?.paragraph ?? 0;
    const e = res.deleted?.entity ?? 0;
    const r = res.deleted?.relation ?? 0;
    message.value = `已执行可恢复删除（单号 ${(res.operation_id || "").slice(0, 12)}）：段落 ${p} · 实体 ${e} · 关系 ${r}`;
    logs.log(`可恢复删除 [${modeName}] 完成：段落 ${p} / 实体 ${e} / 关系 ${r}`, "info");
    app.pushSuccess?.("可恢复删除已执行", "deleteExecute");
    previewResult.value = null;
    if (deleteMode.value !== "clear") selectorInput.value = "";
    await Promise.allSettled([reload(), memory.notifyExternalMutation()]);
  } catch (error) {
    app.pushError(errText(error), "deleteExecute");
  } finally {
    busy.value = false;
  }
}

async function restore(operationId: string): Promise<void> {
  if (busy.value || !operationId) return;
  busy.value = true;
  const scope = graph.effectiveScope();
  try {
    const result = await memoryAdmin<{
      success: boolean;
      restored: number;
      skipped?: string[];
      projection?: { pending: number };
    }>("memory/delete-admin", "restore", { operation_id: operationId }, scope);
    if (scope !== graph.effectiveScope()) return;
    message.value = `已整单恢复 ${result.restored ?? 0} 项，跳过 ${result.skipped?.length || 0} 项，待同步投影 ${result.projection?.pending || 0} 项。`;
    logs.log(`删除操作已恢复 [${operationId.slice(0, 12)}]：还原 ${result.restored ?? 0} 项`, "info");
    app.pushSuccess?.(`已恢复 ${result.restored ?? 0} 项记忆`, "restoreDeleteOperation");
    await Promise.allSettled([reload(), memory.notifyExternalMutation()]);
  } catch (error) {
    app.pushError(errText(error), "restoreDeleteOperation");
  } finally {
    busy.value = false;
  }
}

async function runPurge(): Promise<void> {
  if (busy.value) return;
  const hrs = Math.max(0, Number(purgeHours.value) ?? 168);
  const confirmed = await app.requestConfirmation({
    title: "永久清理超期归档",
    message:
      hrs <= 0
        ? "确定立即清理当前作用域下所有可恢复删除归档？清理后对应记录将无法再撤销恢复。"
        : `确定永久清理 ${hrs} 小时前产生的删除归档快照？清理后超期记录将不可再恢复。`,
    confirmText: "确认清理归档",
    danger: true,
  });
  if (!confirmed) return;

  busy.value = true;
  const scope = graph.effectiveScope();
  try {
    const res = await memoryAdmin<DeletePurgeResult>(
      "memory/delete-admin",
      "purge",
      { older_than_hours: hrs, limit: 1000 },
      scope,
    );
    if (scope !== graph.effectiveScope()) return;
    const count = res.count ?? res.purged?.length ?? 0;
    message.value = `归档清理完成：共永久释放 ${count} 笔超期删除归档。`;
    logs.log(`删除归档清理完成：清理 ${count} 笔（>${hrs}h）`, "info");
    app.pushSuccess?.(`已清理 ${count} 笔超期删除归档`, "purgeDeleteOperations");
    await Promise.allSettled([reload(), memory.notifyExternalMutation()]);
  } catch (error) {
    app.pushError(errText(error), "purgeDeleteOperations");
  } finally {
    busy.value = false;
  }
}

watch(
  () => graph.effectiveScope(),
  () => {
    items.value = [];
    message.value = "";
    previewResult.value = null;
    void reload();
  },
  { immediate: true },
);

watch(
  () => memory.syncVersion,
  () => {
    void reload();
  },
);
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>可恢复删除与操作审计</h2>
        <span class="section-label">支持影响面演练预览、整单回滚恢复及超期快照清理</span>
      </div>
      <button class="btn sm" type="button" :disabled="busy || loading" @click="reload">
        {{ loading ? "刷新中…" : "刷新记录" }}
      </button>
    </div>

    <!-- 扁平细线工作区（移除多余外框，严格 <= 2 层嵌套） -->
    <div class="workbench-section">
      <div class="workbench-row">
        <label class="field mode-field">
          <span>删除模式</span>
          <select
            v-model="deleteMode"
            class="select"
            :disabled="busy"
            @change="previewResult = null"
          >
            <option value="paragraph">按段落</option>
            <option value="entity">按实体</option>
            <option value="relation">按关系</option>
            <option value="source">按来源批次</option>
            <option value="clear">全量清空（当前作用域）</option>
          </select>
        </label>

        <div class="field" style="flex: 2.5 1 240px">
          <label for="delete-selector-input">目标选择器</label>
          <input
            id="delete-selector-input"
            v-model="selectorInput"
            class="input"
            :placeholder="selectorPlaceholder"
            :disabled="busy || deleteMode === 'clear'"
            @keyup.enter="runPreview"
          />
        </div>

        <div class="workbench-btns">
          <button class="btn primary" type="button" :disabled="busy" @click="runPreview">
            预览影响
          </button>
          <button class="btn danger" type="button" :disabled="busy" @click="runExecute">
            执行删除
          </button>
        </div>
      </div>

      <div v-if="previewResult" class="tags tabular preview-strip">
        <span class="tag warn">预览演练（未落库）</span>
        <span class="tag">模式：{{ deleteModeLabel(previewResult.mode || deleteMode) }}</span>
        <span v-for="chip in previewChips" :key="chip" class="tag">{{ chip }}</span>
        <span v-if="!previewChips.length" class="tag">已计算预计影响，确认后可执行删除</span>
      </div>

      <div class="purge-bar">
        <label class="cell-note" for="delete-purge-hours">归档保留策略：</label>
        <select
          id="delete-purge-hours"
          v-model.number="purgeHours"
          class="select purge-select tabular"
          :disabled="busy"
        >
          <option :value="24">清理 24 小时前归档</option>
          <option :value="72">清理 3 天前归档</option>
          <option :value="168">清理 7 天前归档</option>
          <option :value="720">清理 30 天前归档</option>
          <option :value="0">立即清理全部归档</option>
        </select>
        <button class="btn" type="button" :disabled="busy" @click="runPurge">
          清理超期归档
        </button>
      </div>
    </div>

    <div v-if="message" class="status-banner tabular" role="status">{{ message }}</div>

    <!-- 历史删除审计列表 -->
    <div v-if="loading && !items.length" class="empty">正在读取删除审计记录…</div>
    <div v-else-if="!items.length" class="empty">当前作用域暂无删除审计记录</div>
    <div v-else class="rows op-list">
      <article v-for="item in items" :key="item.operation_id" class="operation">
        <div class="op-main">
          <div class="op-header">
            <strong>{{ deleteModeLabel(item.mode) }}</strong>
            <span class="tag" :class="deleteStatusMeta(item.status).tone">
              {{ deleteStatusMeta(item.status).label }}
            </span>
            <span class="cell-note tabular">{{ formatTs(item.created_at) }}</span>
          </div>

          <div class="tags tabular">
            <span class="tag mono">单号 {{ item.operation_id.slice(0, 16) }}</span>
            <template v-if="item.summary?.counts">
              <span class="tag">段落 {{ item.summary.counts.paragraph || 0 }}</span>
              <span class="tag">实体 {{ item.summary.counts.entity || 0 }}</span>
              <span class="tag">关系 {{ item.summary.counts.relation || 0 }}</span>
            </template>
            <span v-if="item.selector?.length" class="tag">
              目标：{{ item.selector.slice(0, 2).join(", ") }}
            </span>
          </div>
        </div>

        <button
          v-if="item.status === 'executed'"
          class="btn sm"
          type="button"
          :disabled="busy"
          @click="restore(item.operation_id)"
        >
          整单恢复
        </button>
      </article>
    </div>
  </div>
</template>

<style scoped>
.workbench-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 14px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--hairline);
}

.workbench-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
}

.mode-field {
  flex: 1 1 160px;
  min-width: 150px;
}

.workbench-btns {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.preview-strip {
  margin-top: 0;
}

.purge-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
}

.purge-select {
  width: auto;
  min-width: 170px;
}

.status-banner {
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--hairline-strong);
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 13px;
}

.op-list {
  border-top: 1px solid var(--hairline);
}

.operation {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  flex-wrap: wrap;
}

.op-main {
  min-width: 0;
  flex: 1 1 260px;
}

.op-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
