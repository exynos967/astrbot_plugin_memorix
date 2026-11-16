<script setup lang="ts">
// 运行时异常提示横幅：当 Embedding / 向量库自检未通过时提示原因并支持一键重新自检。
defineProps<{
  message: string;
  chipLabel: string;
  chipTone: "ok" | "warn" | "bad";
}>();

const emit = defineEmits<{ refresh: [] }>();
</script>

<template>
  <div class="notice warn runtime-banner">
    <div class="banner-body">
      <span class="tag" :class="chipTone">{{ chipLabel }}</span>
      <strong>{{ message }}</strong>
    </div>
    <button class="btn" type="button" @click="emit('refresh')">
      重新自检
    </button>
  </div>
</template>

<style scoped>
.banner-body {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
