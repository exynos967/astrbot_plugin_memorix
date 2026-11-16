<script lang="ts">
let candidateInputUid = 0;
</script>

<script setup lang="ts">
// 通用候选输入框：绑定显式 label[for] 无障碍语义，并实时将 el.value 传入 cm.onInput(val) 消除单字符滞后。
import { computed, ref } from "vue";
import { useCandidateMenu } from "@/composables/useCandidateMenu";
import type { CandidateItem } from "@/stores/candidate";

const props = defineProps<{
  modelValue: string;
  source: (keyword: string) => CandidateItem[] | Promise<CandidateItem[]>;
  debounceMs?: number;
  placeholder?: string;
  label?: string;
  /** 容器 flex 权重。 */
  flex?: number | string;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
  (e: "choose", item: CandidateItem): void;
}>();

const inputId = `memorix-candidate-input-${++candidateInputUid}`;
const inputRef = ref<HTMLInputElement | null>(null);

const model = computed<string>({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});

const cm = useCandidateMenu({
  inputRef,
  model,
  source: (kw) => props.source(kw),
  debounceMs: props.debounceMs,
  onChoose: (item) => emit("choose", item),
});

const fieldStyle = computed(() =>
  props.flex != null ? { flex: typeof props.flex === "number" ? String(props.flex) : props.flex } : undefined,
);

function handleInput(e: Event): void {
  const val = (e.target as HTMLInputElement).value;
  emit("update:modelValue", val);
  cm.onInput(val);
}
</script>

<template>
  <div class="field" :style="fieldStyle">
    <label v-if="label" :for="inputId">{{ label }}</label>
    <input
      :id="inputId"
      ref="inputRef"
      :value="modelValue"
      class="input"
      :placeholder="placeholder"
      :aria-label="label || placeholder || '候选输入'"
      autocomplete="off"
      @input="handleInput"
      @focus="cm.open()"
    />
  </div>
</template>
