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
 * 候选菜单 composable：
 * - onInput(val?) 直接接收实时 DOM 输入值，消除 v-model prop 异步回传导致的 1 键滞后
 * - 维护请求序列号 reqSeq + try/catch 保护，杜绝异步竞态乱序与未捕获 Promise 拒绝
 */
export function useCandidateMenu(opts: UseCandidateMenuOptions) {
  const store = useCandidateStore();
  let timer: number | null = null;
  let reqSeq = 0;

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

  async function refresh(explicitKeyword?: string): Promise<void> {
    const el = opts.inputRef.value;
    if (!el) return;
    const kw = explicitKeyword !== undefined ? explicitKeyword : (el.value ?? opts.model.value ?? "");
    const seq = ++reqSeq;
    try {
      const list = await opts.source(kw);
      if (seq !== reqSeq) return;
      if (store.inputEl === el && store.visible) {
        store.setItems(list);
      }
    } catch {
      if (seq === reqSeq && store.inputEl === el && store.visible) {
        store.setItems([]);
      }
    }
  }

  function open(): void {
    clearTimer();
    const el = opts.inputRef.value;
    if (!el) return;
    store.attach(el, [], makeChooseHandler());
    void refresh(el.value ?? opts.model.value ?? "");
  }

  function onInput(val?: string | Event): void {
    clearTimer();
    const el = opts.inputRef.value;
    if (!el) return;
    let freshVal: string;
    if (typeof val === "string") {
      freshVal = val;
    } else if (val && typeof val === "object" && "target" in val && val.target) {
      freshVal = (val.target as HTMLInputElement).value ?? el.value ?? "";
    } else {
      freshVal = el.value ?? opts.model.value ?? "";
    }
    if (!store.visible || store.inputEl !== el) {
      store.attach(el, [], makeChooseHandler());
    }
    if (opts.debounceMs && opts.debounceMs > 0) {
      timer = window.setTimeout(() => void refresh(freshVal), opts.debounceMs);
    } else {
      void refresh(freshVal);
    }
  }

  onBeforeUnmount(() => {
    clearTimer();
    reqSeq += 1;
    if (store.inputEl === opts.inputRef.value) store.detach();
  });

  return { open, onInput };
}
