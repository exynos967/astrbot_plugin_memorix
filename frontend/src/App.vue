<script setup lang="ts">
import AppShell from "@/components/shell/AppShell.vue";
import { useTheme } from "@/composables/useTheme";
import { useAppStore } from "@/stores/app";

// 主题/effects 同步到 <html data-theme/data-effects>，持久化经 safeStorage（修 H7）。
useTheme();

const app = useAppStore();
</script>

<template>
  <AppShell />

  <!-- 全局错误 toast：修复 legacy 多处 catch 静默吞错 -->
  <div class="error-toast-stack" aria-live="polite">
    <div v-for="err in app.errors" :key="err.id" class="error-toast" @click="app.dismiss(err.id)">
      <strong>{{ err.source }}</strong>
      <span>{{ err.message }}</span>
    </div>
  </div>
</template>

<style scoped>
.error-toast-stack {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 360px;
}

.error-toast {
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--surface-strong);
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--red-soft);
  color: var(--text);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
}

.error-toast strong {
  color: var(--red);
}
</style>
