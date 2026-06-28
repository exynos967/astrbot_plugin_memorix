<script setup lang="ts">
// 操作日志 view：渲染 useLogsStore.entries，支持清空。
// 从 legacy view-logs（index.html 行 2423-2434）+ log() 迁移。
import { useLogsStore } from "@/stores/logs";

const logs = useLogsStore();

function levelClass(level: string): string {
  if (level === "error") return "bad";
  if (level === "warn") return "warn";
  return "ok";
}
</script>

<template>
  <section class="logs-view">
    <div class="band">
      <div class="panel-title">
        <h2>操作日志</h2>
        <div class="toolbar">
          <span class="section-label">仅保留近 200 条</span>
          <button class="btn" @click="logs.clear()">清空日志</button>
        </div>
      </div>
      <div class="log">
        <div v-for="entry in logs.entries" :key="entry.id">
          <span class="tag" :class="levelClass(entry.level)">{{ entry.time }}</span>
          <span class="log-msg">{{ entry.message }}</span>
        </div>
        <div v-if="!logs.entries.length" class="empty">暂无日志</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.logs-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.band {
  padding: 20px 24px;
  border-radius: var(--radius-lg);
  background: var(--surface);
  backdrop-filter: var(--blur-soft);
  border: 1px solid var(--hairline);
}

.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.panel-title h2 {
  margin: 0;
  font-size: 16px;
  color: var(--ink);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.section-label {
  color: var(--muted-2);
  font-size: 12px;
}

.btn {
  padding: 6px 14px;
  border-radius: 10px;
  border: 1px solid var(--hairline);
  background: var(--surface-strong);
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
}

.btn:hover {
  border-color: var(--hairline-strong);
}

.log {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 60vh;
  overflow-y: auto;
  font-size: 13px;
}

.log > div {
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 4px 0;
  border-bottom: 1px solid var(--hairline);
}

.tag {
  padding: 1px 8px;
  border-radius: 6px;
  font-size: 11px;
  flex-shrink: 0;
}

.tag.ok {
  background: var(--green-soft);
  color: var(--green);
}

.tag.warn {
  background: var(--amber-soft);
  color: var(--amber);
}

.tag.bad {
  background: var(--red-soft);
  color: var(--red);
}

.log-msg {
  color: var(--text);
  word-break: break-all;
}

.empty {
  color: var(--muted-2);
  padding: 24px;
  text-align: center;
}
</style>
