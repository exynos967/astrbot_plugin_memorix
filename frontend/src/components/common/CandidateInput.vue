<script setup lang="ts">
// 通用候选输入框：把 useCandidateMenu 接线封装为 v-model 组件，消除多输入框重复代码（DRY）。
// 用法：<CandidateInput v-model="store.field" :source="fn" :debounce-ms="180" placeholder="…" label="…" />
// source：根据 keyword 返回候选（同步或异步），与 useCandidateMenu.source 同义。
import { computed, ref } from "vue";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import type { CandidateItem } from "@/stores/candidate";

const props = defineProps<{
  modelValue: string;
  source: (keyword: string) => CandidateItem[] | Promise<CandidateItem[]>;
  debounceMs?: number;
  placeholder?: string;
  label?: string;
  /** 容器 flex 权重（与 legacy .field style="flex:N" 对齐）。 */
  flex?: number | string;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
  (e: "choose", item: CandidateItem): void;
}>();

const inputRef = ref<HTMLInputElement | null>(null);

// computed get/set 作 useCandidateMenu 的 model Ref：choose 时写回 → emit 到父级。
const model = computed<string>({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});

const cm = useCandidateMenu({
  inputRef,
  model,
  source: props.source,
  debounceMs: props.debounceMs,
  onChoose: (item) => emit("choose", item),
});

const fieldStyle = computed(() =>
  props.flex != null ? { flex: typeof props.flex === "number" ? String(props.flex) : props.flex } : undefined,
);
</script>

<template>
  <div class="field" :style="fieldStyle">
    <label v-if="label">{{ label }}</label>
    <input
      ref="inputRef"
      :value="modelValue"
      class="input"
      :placeholder="placeholder"
      autocomplete="off"
      @input="(e) => { emit('update:modelValue', (e.target as HTMLInputElement).value); cm.onInput(); }"
      @focus="cm.open()"
    />
  </div>
</template>
