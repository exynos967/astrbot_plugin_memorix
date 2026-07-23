<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useAppStore } from "@/stores/app";

const app = useAppStore();
const { confirmation } = storeToRefs(app);
const cancelButton = ref<HTMLButtonElement | null>(null);

function resolve(confirmed: boolean): void {
  app.resolveConfirmation(confirmed);
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && confirmation.value) resolve(false);
}

watch(confirmation, async (request) => {
  if (!request) return;
  await nextTick();
  cancelButton.value?.focus();
});

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
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
  padding: 20px;
  background: rgba(7, 17, 31, 0.58);
  backdrop-filter: blur(8px);
}

.confirm-dialog {
  width: min(440px, 100%);
  padding: 22px;
  border: 1px solid var(--hairline-strong);
  border-radius: var(--radius-lg);
  color: var(--text);
  background: var(--surface-strong);
  box-shadow: var(--shadow);
}

.confirm-dialog h2 {
  margin: 0;
  color: var(--ink);
  font-size: 18px;
}

.confirm-dialog p {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 22px;
}
</style>
