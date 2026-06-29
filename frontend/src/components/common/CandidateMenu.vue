<script setup lang="ts">
// 全局候选菜单（单例，在 App.vue 挂载一份；teleport 到 body）。
//
// 修复 H8（候选菜单内部滚动链传播到外部 .view-stack）：
//   1. CSS overscroll-behavior: contain —— 滚动链隔离。
//   2. @wheel 处理：stopPropagation 始终阻止冒泡；菜单不可滚动时 preventDefault，
//      到顶/到底时 preventDefault —— 不让多余滚动量漏给外部容器。
//
// 键盘导航（ArrowUp/Down/Enter/Escape）在 capture 阶段拦截，stopPropagation 阻止
// 输入框自身 Enter 处理器重复触发（如 people 关键词 Enter 触发查询时，菜单开着则
// 改为选中候选项）。外部 mousedown 关闭；window scroll/resize 重定位（菜单内滚动除外）。
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useCandidateStore } from "@/stores/candidate";

const store = useCandidateStore();
const menuRef = ref<HTMLElement | null>(null);

const MAX_HEIGHT = 250;

const menuStyle = computed<Record<string, string>>((): Record<string, string> => {
  const a = store.anchor;
  if (!a) return { display: "none" };
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = a.width;
  const spaceBelow = vh - a.bottom;
  const showBelow = spaceBelow >= 160 || spaceBelow >= a.top;
  const top = showBelow ? a.bottom + 4 : Math.max(8, a.top - MAX_HEIGHT - 4);
  let left = a.left;
  if (left + width > vw - 8) left = vw - width - 8;
  if (left < 8) left = 8;
  return {
    display: store.visible && store.items.length > 0 ? "block" : "none",
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    width: `${Math.round(width)}px`,
    maxHeight: `${MAX_HEIGHT}px`,
  };
});

/** H8 核心：菜单内 wheel 隔离。 */
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
  if (t && menuRef.value?.contains(t)) return; // 菜单内滚动不重定位（H8）
  store.reposition();
}

function onResize(): void {
  if (store.visible) store.reposition();
}

onMounted(() => {
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
        @mouseenter="hover(idx)"
        @mousedown.prevent="pick(idx)"
      >
        <span class="candidate-value">{{ item.value }}</span>
        <span v-if="item.kind" class="candidate-kind">{{ item.kind }}</span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.candidate-menu {
  position: fixed;
  z-index: 9000;
  overflow: auto;
  /* H8 修复：滚动链隔离，菜单内滚动不传播到外部 .view-stack */
  overscroll-behavior: contain;
  scrollbar-width: none;
  padding: 7px;
  border: 1px solid var(--hairline);
  border-radius: 16px;
  background: var(--surface-strong);
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.candidate-menu::-webkit-scrollbar {
  width: 0;
  height: 0;
  display: none;
}

.candidate-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 38px;
  padding: 7px 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  font-size: 13px;
}
.candidate-row:hover,
.candidate-row.active {
  background: var(--accent-soft);
  color: var(--accent-ink);
}

.candidate-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.candidate-kind {
  flex: none;
  font-size: 11px;
  color: var(--muted);
}
</style>
