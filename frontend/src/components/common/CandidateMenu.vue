<script setup lang="ts">
// 全局候选菜单（单例，teleport 到 body）：
// - 响应式视口边界钳制（窄屏与矮视口下均严格贴合上下左右安全边距，绝不悬空或溢出）
// - 键盘上下导航自动 scrollIntoView({ block: "nearest" })
// - wheel 滚动链隔离
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useCandidateStore } from "@/stores/candidate";

const store = useCandidateStore();
const menuRef = ref<HTMLElement | null>(null);

const MAX_HEIGHT = 256;
const EDGE_GAP = 8;

const viewport = ref({
  w: typeof window !== "undefined" ? window.innerWidth || 360 : 360,
  h: typeof window !== "undefined" ? window.innerHeight || 640 : 640,
});

function syncViewport(): void {
  if (typeof window === "undefined") return;
  viewport.value = {
    w: Math.max(window.innerWidth || 320, 200),
    h: Math.max(window.innerHeight || 480, 200),
  };
}

const menuStyle = computed<Record<string, string>>((): Record<string, string> => {
  const a = store.anchor;
  if (!a || !store.visible || store.items.length === 0) {
    return { display: "none" };
  }

  const vw = viewport.value.w;
  const vh = viewport.value.h;
  const maxAllowedWidth = Math.max(140, vw - EDGE_GAP * 2);
  const desiredMinWidth = Math.min(220, maxAllowedWidth);
  const width = Math.min(Math.max(a.width, desiredMinWidth), maxAllowedWidth);

  let left = a.left;
  if (left + width > vw - EDGE_GAP) left = vw - width - EDGE_GAP;
  if (left < EDGE_GAP) left = EDGE_GAP;

  const spaceBelow = Math.max(0, vh - a.bottom - EDGE_GAP - 4);
  const spaceAbove = Math.max(0, a.top - EDGE_GAP - 4);
  const showBelow = spaceBelow >= 160 || spaceBelow >= spaceAbove;
  const availableHeight = showBelow ? spaceBelow : spaceAbove;
  const clampedMaxHeight = Math.max(96, Math.min(MAX_HEIGHT, availableHeight));

  const pos: Record<string, string> = {
    display: "flex",
    left: `${Math.round(left)}px`,
    width: `${Math.round(width)}px`,
    maxHeight: `${Math.round(clampedMaxHeight)}px`,
  };

  if (showBelow) {
    pos.top = `${Math.round(Math.max(EDGE_GAP, a.bottom + 4))}px`;
  } else {
    pos.bottom = `${Math.round(Math.max(EDGE_GAP, vh - a.top + 4))}px`;
  }

  return pos;
});

watch(
  () => store.activeIndex,
  async (idx) => {
    if (!store.visible) return;
    await nextTick();
    const container = menuRef.value;
    if (!container) return;
    const target = container.querySelectorAll<HTMLElement>(".candidate-row")[idx];
    target?.scrollIntoView({ block: "nearest" });
  },
);

function onWheel(e: WheelEvent): void {
  const el = menuRef.value;
  if (!el) return;
  e.stopPropagation();
  const canScroll = el.scrollHeight > el.clientHeight;
  if (!canScroll) {
    e.preventDefault();
    return;
  }
  const atTop = el.scrollTop <= 0 && e.deltaY < 0;
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight && e.deltaY > 0;
  if (atTop || atBottom) e.preventDefault();
}

function onKeydown(e: KeyboardEvent): void {
  if (!store.visible || !store.inputEl) return;
  if (document.activeElement !== store.inputEl) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    e.stopPropagation();
    store.move(1);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    e.stopPropagation();
    store.move(-1);
  } else if (e.key === "Enter") {
    if (store.items.length) {
      e.preventDefault();
      e.stopPropagation();
      store.choose(store.activeIndex);
    }
  } else if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    store.detach();
  }
}

function onDocDown(e: MouseEvent): void {
  if (!store.visible) return;
  const t = e.target as Node | null;
  if (!t) return;
  if (menuRef.value?.contains(t)) return;
  if (store.inputEl && t === store.inputEl) return;
  store.detach();
}

function onScroll(e: Event): void {
  if (!store.visible) return;
  const t = e.target as Node | null;
  if (t && menuRef.value?.contains(t)) return;
  store.reposition();
}

function onResize(): void {
  syncViewport();
  if (store.visible) store.reposition();
}

onMounted(() => {
  syncViewport();
  document.addEventListener("keydown", onKeydown, true);
  document.addEventListener("mousedown", onDocDown, true);
  window.addEventListener("scroll", onScroll, true);
  window.addEventListener("resize", onResize);
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown, true);
  document.removeEventListener("mousedown", onDocDown, true);
  window.removeEventListener("scroll", onScroll, true);
  window.removeEventListener("resize", onResize);
});

function pick(index: number): void {
  store.choose(index);
}

function hover(index: number): void {
  store.activeIndex = index;
}
</script>

<template>
  <Teleport to="body">
    <div
      ref="menuRef"
      class="candidate-menu"
      role="listbox"
      aria-label="候选补全列表"
      :style="menuStyle"
      @wheel="onWheel"
    >
      <button
        v-for="(item, idx) in store.items"
        :key="item.value + ':' + idx"
        class="candidate-row"
        :class="{ active: idx === store.activeIndex }"
        type="button"
        role="option"
        :aria-selected="idx === store.activeIndex"
        :title="item.label || item.value"
        @mouseenter="hover(idx)"
        @mousedown.prevent="pick(idx)"
      >
        <span class="candidate-value">{{ item.label || item.value }}</span>
        <span v-if="item.kind || item.hint" class="candidate-kind">{{ item.kind || item.hint }}</span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.candidate-menu {
  position: fixed;
  z-index: 9200;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
  padding: 4px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-sm);
  flex-direction: column;
  gap: 2px;
}

.candidate-row {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 36px;
  padding: 6px 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  font-size: 13px;
  transition:
    background-color var(--m-out) var(--ease),
    color var(--m-out) var(--ease);
}

.candidate-row:hover,
.candidate-row.active {
  background: var(--accent-soft);
  color: var(--accent-ink);
}

.candidate-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.candidate-kind {
  flex: none;
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 768px) {
  .candidate-row {
    min-height: 40px;
  }
}
</style>
