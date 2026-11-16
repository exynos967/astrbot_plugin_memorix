import { defineStore } from "pinia";
import { ref, shallowRef } from "vue";

/**
 * 候选菜单 store（全局单例，由 CandidateMenu.vue 渲染）。
 */
export interface CandidateItem {
  value: string;
  label?: string;
  hint?: string;
  kind?: string;
}

interface AnchorRect {
  left: number;
  top: number;
  bottom: number;
  width: number;
}

export const useCandidateStore = defineStore("candidate", () => {
  const visible = ref(false);
  const items = ref<CandidateItem[]>([]);
  const activeIndex = ref(0);
  const inputEl = shallowRef<HTMLInputElement | null>(null);
  const anchor = ref<AnchorRect | null>(null);

  let chooseHandler: ((item: CandidateItem) => void) | null = null;

  function readAnchor(el: HTMLElement): AnchorRect {
    const r = el.getBoundingClientRect();
    return { left: r.left, top: r.top, bottom: r.bottom, width: r.width };
  }

  function attach(
    el: HTMLInputElement,
    list: CandidateItem[],
    onChoose?: (item: CandidateItem) => void,
  ): void {
    inputEl.value = el;
    items.value = Array.isArray(list) ? list : [];
    activeIndex.value = 0;
    anchor.value = readAnchor(el);
    chooseHandler = onChoose ?? null;
    visible.value = true;
  }

  function setItems(list: CandidateItem[]): void {
    items.value = Array.isArray(list) ? list : [];
    if (activeIndex.value >= items.value.length) activeIndex.value = 0;
  }

  function reposition(): void {
    const el = inputEl.value;
    if (!el) return;
    anchor.value = readAnchor(el);
  }

  function move(delta: number): void {
    const n = items.value.length;
    if (!n) return;
    activeIndex.value = (activeIndex.value + delta + n) % n;
  }

  function choose(index: number): void {
    const item = items.value[index];
    if (item && chooseHandler) chooseHandler(item);
    detach();
  }

  function detach(): void {
    visible.value = false;
    inputEl.value = null;
    anchor.value = null;
    chooseHandler = null;
  }

  return {
    visible,
    items,
    activeIndex,
    inputEl,
    anchor,
    attach,
    setItems,
    reposition,
    move,
    choose,
    detach,
  };
});
