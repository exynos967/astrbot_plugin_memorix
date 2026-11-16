<script setup lang="ts">
// 页面内确认对话框：兼容 AstrBot sandbox iframe 无原生模态框约束，带 Esc 关闭与 Tab 焦点环闭环。
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useAppStore } from "@/stores/app";

const app = useAppStore();
const { confirmation } = storeToRefs(app);
const cancelButton = ref<HTMLButtonElement | null>(null);
const confirmButton = ref<HTMLButtonElement | null>(null);

function resolve(confirmed: boolean): void {
  app.resolveConfirmation(confirmed);
}

function onKeydown(event: KeyboardEvent): void {
  if (!confirmation.value) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    resolve(false);
    return;
  }
  if (event.key === "Tab") {
    const cancelEl = cancelButton.value;
    const confirmEl = confirmButton.value;
    if (!cancelEl || !confirmEl) return;
    const active = document.activeElement;
    if (event.shiftKey) {
      if (active === cancelEl || (active !== confirmEl && active !== cancelEl)) {
        event.preventDefault();
        confirmEl.focus();
      }
    } else if (active === confirmEl || (active !== confirmEl && active !== cancelEl)) {
      event.preventDefault();
      cancelEl.focus();
    }
  }
}

watch(confirmation, async (request) => {
  if (!request) return;
  await nextTick();
  // 危险操作默认聚焦取消按钮防误触；常规确认聚焦主操作按钮
  if (request.danger) {
    cancelButton.value?.focus();
  } else {
    (confirmButton.value ?? cancelButton.value)?.focus();
  }
});

onMounted(() => window.addEventListener("keydown", onKeydown, true));
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown, true);
  if (confirmation.value) resolve(false);
});
</script>

<template>
  <Teleport to="body">
    <div v-if="confirmation" class="confirm-overlay" @click.self="resolve(false)">
      <section
        class="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-message"
      >
        <h2 id="confirm-dialog-title">{{ confirmation.title }}</h2>
        <p id="confirm-dialog-message">{{ confirmation.message }}</p>
        <div class="confirm-actions">
          <button ref="cancelButton" class="btn" type="button" @click="resolve(false)">
            {{ confirmation.cancelText }}
          </button>
          <button
            ref="confirmButton"
            class="btn"
            :class="confirmation.danger ? 'danger' : 'primary'"
            type="button"
            @click="resolve(true)"
          >
            {{ confirmation.confirmText }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: grid;
  place-items: center;
  padding: 16px;
  background: var(--bg-overlay);
}

.confirm-dialog {
  width: min(440px, 100%);
  padding: 20px 22px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  color: var(--text);
  background: var(--bg-elevated);
  box-shadow: var(--shadow);
  animation: confirmIn 160ms var(--ease-enter);
}

@keyframes confirmIn {
  from {
    opacity: 0;
    transform: translateY(4px) scale(0.99);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .confirm-dialog {
    animation: none;
  }
}

.confirm-dialog h2 {
  margin: 0;
  color: var(--ink);
  font-size: 16.5px;
  font-weight: 650;
  letter-spacing: -0.015em;
}

.confirm-dialog p {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 13.5px;
  line-height: 1.6;
  white-space: pre-line;
  overflow-wrap: anywhere;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}

@media (max-width: 480px) {
  .confirm-dialog {
    padding: 18px;
  }

  .confirm-actions {
    flex-direction: column-reverse;
  }

  .confirm-actions .btn {
    width: 100%;
    min-height: 40px;
  }
}
</style>
