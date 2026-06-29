import { onBeforeUnmount, type Ref } from "vue";
import { useCandidateStore, type CandidateItem } from "@/stores/candidate";

export interface UseCandidateMenuOptions {
  /** 绑定候选行为的输入框 ref（组件内 template ref）。 */
  inputRef: Ref<HTMLInputElement | null>;
  /** 输入框的 v-model 值（choose 时写回）。 */
  model: Ref<string>;
  /** 候选项来源：根据 keyword 返回候选项（同步或异步）。 */
  source: (keyword: string) => CandidateItem[] | Promise<CandidateItem[]>;
  /** 输入防抖毫秒（异步来源建议 150-200；同步来源传 0）。 */
  debounceMs?: number;
  /** choose 后的额外副作用（如联动查询）。 */
  onChoose?: (item: CandidateItem) => void;
}

/**
 * 候选菜单 composable：把一个输入框接入全局候选菜单。
 *
 * - open()：focus 时调用，attach 菜单 + 立即拉取候选项。
 * - onInput()：input 事件时调用，按 debounceMs 刷新候选项。
 * - onBeforeUnmount：清定时器 + 若菜单仍属本输入框则 detach。
 *
 * 修复 H8 的滚动隔离在 CandidateMenu.vue 层完成；本 composable 仅负责数据供给。
 */
export function useCandidateMenu(opts: UseCandidateMenuOptions) {
  const store = useCandidateStore();
  let timer: number | null = null;

  function clearTimer(): void {
    if (timer != null) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  function makeChooseHandler(): (item: CandidateItem) => void {
    return (item: CandidateItem) => {
      opts.model.value = item.value;
      opts.onChoose?.(item);
    };
  }

  async function refresh(): Promise<void> {
    const el = opts.inputRef.value;
    if (!el) return;
    const kw = opts.model.value;
    const list = await opts.source(kw);
    // 仅在菜单仍由本输入框持有时刷新（避免异步竞态写错菜单）
    if (store.inputEl === el && store.visible) {
      store.setItems(list);
    }
  }

  function open(): void {
    clearTimer();
    const el = opts.inputRef.value;
    if (!el) return;
    store.attach(el, [], makeChooseHandler());
    void refresh();
  }

  function onInput(): void {
    clearTimer();
    const el = opts.inputRef.value;
    if (!el) return;
    if (!store.visible || store.inputEl !== el) {
      store.attach(el, [], makeChooseHandler());
    }
    if (opts.debounceMs && opts.debounceMs > 0) {
      timer = window.setTimeout(() => void refresh(), opts.debounceMs);
    } else {
      void refresh();
    }
  }

  onBeforeUnmount(() => {
    clearTimer();
    if (store.inputEl === opts.inputRef.value) store.detach();
  });

  return { open, onInput };
}
