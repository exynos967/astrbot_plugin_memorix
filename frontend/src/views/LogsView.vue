<script setup lang="ts">
// 操作日志视图：支持多级别筛选（全部/信息/成功/警告/错误）、关键词检索、复制与清空。
import { computed, ref } from "vue";
import { copyTextSafe } from "@/services/api";
import { useAppStore } from "@/stores/app";
import { useLogsStore, type LogEntry, type LogLevel } from "@/stores/logs";

type FilterLevel = "all" | LogLevel;

const logs = useLogsStore();
const app = useAppStore();

const levelFilter = ref<FilterLevel>("all");
const keyword = ref("");

const LEVEL_META: Record<LogLevel, { label: string; tagClass: string }> = {
  info: { label: "信息", tagClass: "info" },
  success: { label: "成功", tagClass: "ok" },
  warn: { label: "警告", tagClass: "warn" },
  error: { label: "错误", tagClass: "bad" },
};

const counts = computed(() => {
  const c = { all: logs.entries.length, info: 0, success: 0, warn: 0, error: 0 };
  for (const item of logs.entries) {
    c[item.level] = (c[item.level] || 0) + 1;
  }
  return c;
});

const filteredEntries = computed<LogEntry[]>(() => {
  const kw = keyword.value.trim().toLowerCase();
  return logs.entries.filter((entry) => {
    if (levelFilter.value !== "all" && entry.level !== levelFilter.value) {
      return false;
    }
    if (!kw) return true;
    return (
      entry.message.toLowerCase().includes(kw) ||
      entry.time.toLowerCase().includes(kw) ||
      Boolean(entry.source && entry.source.toLowerCase().includes(kw))
    );
  });
});

async function handleCopyAll(): Promise<void> {
  if (!filteredEntries.value.length) return;
  const text = filteredEntries.value
    .map((e) => `[${e.time}] [${LEVEL_META[e.level]?.label ?? e.level}]${e.source ? ` [${e.source}]` : ""} ${e.message}`)
    .join("\n");
  const ok = await copyTextSafe(text);
  if (ok) {
    app.pushSuccess(`已复制 ${filteredEntries.value.length} 条日志到剪贴板`, "操作日志");
  } else {
    app.pushError("剪贴板复制失败，请检查浏览器权限", "操作日志");
  }
}

async function handleClear(): Promise<void> {
  if (!logs.entries.length) return;
  const confirmed = await app.requestConfirmation({
    title: "清空操作日志",
    message: `确定清空当前记录的 ${logs.entries.length} 条前端活动日志吗？`,
    confirmText: "清空日志",
    danger: true,
  });
  if (confirmed) {
    logs.clear();
  }
}
</script>

<template>
  <section class="logs-view">
    <div class="band">
      <div class="panel-title">
        <div>
          <h2 class="tabular">操作与异常日志（{{ filteredEntries.length }} / {{ counts.all }}）</h2>
          <span class="section-label">保留最近 300 条控制台操作与请求异常记录</span>
        </div>
        <div class="logs-actions">
          <button class="btn primary" type="button" :disabled="!filteredEntries.length" @click="handleCopyAll">
            复制当前日志
          </button>
          <button class="btn danger" type="button" :disabled="!logs.entries.length" @click="handleClear">
            清空日志
          </button>
        </div>
      </div>

      <div class="logs-filter-bar">
        <div class="segmented" role="tablist" aria-label="日志等级筛选">
          <button
            type="button"
            role="tab"
            :aria-selected="levelFilter === 'all'"
            :class="{ active: levelFilter === 'all' }"
            @click="levelFilter = 'all'"
          >
            <span class="tabular">全部（{{ counts.all }}）</span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="levelFilter === 'info'"
            :class="{ active: levelFilter === 'info' }"
            @click="levelFilter = 'info'"
          >
            <span class="tabular">信息（{{ counts.info }}）</span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="levelFilter === 'success'"
            :class="{ active: levelFilter === 'success' }"
            @click="levelFilter = 'success'"
          >
            <span class="tabular">成功（{{ counts.success }}）</span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="levelFilter === 'warn'"
            :class="{ active: levelFilter === 'warn' }"
            @click="levelFilter = 'warn'"
          >
            <span class="tabular">警告（{{ counts.warn }}）</span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="levelFilter === 'error'"
            :class="{ active: levelFilter === 'error' }"
            @click="levelFilter = 'error'"
          >
            <span class="tabular">错误（{{ counts.error }}）</span>
          </button>
        </div>

        <div class="field logs-search">
          <input
            v-model="keyword"
            class="input"
            type="search"
            placeholder="搜索日志内容、来源或时间…"
            aria-label="搜索日志关键词"
          />
        </div>
      </div>

      <div v-if="filteredEntries.length" class="log-list">
        <div v-for="entry in filteredEntries" :key="entry.id" class="log-row">
          <span class="mono tabular log-time">{{ entry.time }}</span>
          <span class="tag" :class="LEVEL_META[entry.level]?.tagClass || 'info'">
            {{ LEVEL_META[entry.level]?.label || entry.level }}
          </span>
          <span v-if="entry.source" class="tag">{{ entry.source }}</span>
          <span class="log-msg">{{ entry.message }}</span>
        </div>
      </div>
      <div v-else class="empty-state">
        <strong>{{ logs.entries.length ? "未找到匹配的日志条目" : "暂无操作日志" }}</strong>
        <p>{{ logs.entries.length ? "请切换等级标签或清空搜索关键词。" : "执行查询、修改设置、导入知识或治理资产时的操作轨迹会在此实时记录。" }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.logs-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.logs-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.logs-filter-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.logs-search {
  flex: 1 1 220px;
  max-width: 360px;
}

.log-list {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 280px);
  min-height: 240px;
  overflow-y: auto;
  border-top: 1px solid var(--hairline);
}

.log-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  padding: 9px 4px;
  border-bottom: 1px solid var(--hairline);
  font-size: 13px;
}

.log-time {
  color: var(--muted);
  font-size: 12px;
  flex-shrink: 0;
}

.log-msg {
  flex: 1 1 240px;
  min-width: 0;
  color: var(--text);
  overflow-wrap: anywhere;
  line-height: 1.5;
}

@media (max-width: 768px) {
  .logs-search {
    max-width: none;
    width: 100%;
  }
}
</style>
