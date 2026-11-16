<script setup lang="ts">
import AppShell from "@/components/shell/AppShell.vue";
import CandidateMenu from "@/components/common/CandidateMenu.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import { useTheme } from "@/composables/useTheme";
import { useAppStore, type ToastTone } from "@/stores/app";

useTheme();

const app = useAppStore();

function toneLabel(tone: ToastTone): string {
  if (tone === "success") return "已完成";
  if (tone === "warn") return "注意";
  if (tone === "error") return "错误";
  return "提示";
}
</script>

<template>
  <AppShell />

  <!-- 全局候选菜单（单例，teleport 到 body） -->
  <CandidateMenu />

  <!-- 页面内确认弹窗（兼容 sandbox iframe 禁用原生 confirm） -->
  <ConfirmDialog />

  <!-- 全局四色语义通知栈（悬停暂停计时、点击或键盘确认关闭） -->
  <TransitionGroup tag="div" name="toast" class="toast-stack" aria-live="polite">
    <div
      v-for="item in app.toasts"
      :key="item.id"
      class="toast-card"
      :class="`tone-${item.tone}`"
      role="status"
      tabindex="0"
      :aria-label="`${item.source || toneLabel(item.tone)}：${item.message}（点击关闭）`"
      @mouseenter="app.pauseToast(item.id)"
      @mouseleave="app.resumeToast(item.id)"
      @click="app.dismissToast(item.id)"
      @keydown.enter.prevent="app.dismissToast(item.id)"
      @keydown.space.prevent="app.dismissToast(item.id)"
    >
      <span class="toast-icon" aria-hidden="true">
        <svg v-if="item.tone === 'success'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20 6 9 17l-5-5" />
        </svg>
        <svg v-else-if="item.tone === 'warn'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
        </svg>
        <svg v-else-if="item.tone === 'error'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="9" />
          <path d="m15 9-6 6m0-6 6 6" />
        </svg>
        <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 16v-4m0-4h.01" />
        </svg>
      </span>
      <div class="toast-body">
        <strong>{{ item.source || toneLabel(item.tone) }}</strong>
        <span>{{ item.message }}</span>
      </div>
    </div>
  </TransitionGroup>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(376px, calc(100vw - 32px));
  pointer-events: none;
}

.toast-card {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 11px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border-color);
  border-left-width: 3px;
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
  line-height: 1.45;
  transition:
    transform var(--m-out) var(--ease),
    border-color var(--m-out) var(--ease),
    opacity var(--m-out) var(--ease);
}

@media (hover: hover) and (pointer: fine) {
  .toast-card:hover {
    transform: translateY(-1px);
    border-color: var(--border-hover);
    transition-duration: var(--m-in);
  }
}

.toast-enter-active {
  transition:
    opacity 160ms var(--ease-enter),
    transform 160ms var(--ease-enter);
}

.toast-leave-active {
  transition:
    opacity var(--m-out) var(--ease),
    transform var(--m-out) var(--ease);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

@media (prefers-reduced-motion: reduce) {
  .toast-enter-active,
  .toast-leave-active {
    transition: opacity 100ms linear;
  }
  .toast-enter-from,
  .toast-leave-to {
    transform: none;
  }
}

.toast-icon {
  margin-top: 2px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
}

.toast-body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-wrap: anywhere;
}

.toast-body strong {
  font-size: 12.5px;
  font-weight: 650;
}

.toast-card.tone-success {
  border-left-color: var(--green);
}
.toast-card.tone-success .toast-icon,
.toast-card.tone-success strong {
  color: var(--green);
}

.toast-card.tone-info {
  border-left-color: var(--blue);
}
.toast-card.tone-info .toast-icon,
.toast-card.tone-info strong {
  color: var(--blue);
}

.toast-card.tone-warn {
  border-left-color: var(--amber);
}
.toast-card.tone-warn .toast-icon,
.toast-card.tone-warn strong {
  color: var(--amber);
}

.toast-card.tone-error {
  border-left-color: var(--red);
}
.toast-card.tone-error .toast-icon,
.toast-card.tone-error strong {
  color: var(--red);
}

@media (max-width: 480px) {
  .toast-stack {
    right: 12px;
    left: 12px;
    bottom: 12px;
    width: auto;
  }
}
</style>
