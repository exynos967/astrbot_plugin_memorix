<script setup lang="ts">
// 界面偏好面板：主题与界面渲染模式，持久化至本地浏览器存储（沙盒受限时自动降级为会话内存）。
import { computed } from "vue";
import { useUiStore } from "@/stores/ui";
import { realStorageAvailable } from "@/utils/storage";

const ui = useUiStore();

const storageOk = computed(() => realStorageAvailable());
const statusText = computed(() => (storageOk.value ? "本地已保存" : "仅当前会话有效"));
</script>

<template>
  <div class="band">
    <div class="panel-title">
      <div>
        <h2>界面偏好</h2>
        <span class="section-label">仅作用于当前浏览器视图，不修改服务端配置</span>
      </div>
      <span class="tag">{{ statusText }}</span>
    </div>
    <div class="settings-fields pref-grid">
      <div class="field">
        <label for="ui-pref-theme">主题配色</label>
        <select
          id="ui-pref-theme"
          class="select"
          :value="ui.theme"
          @change="ui.setTheme(($event.target as HTMLSelectElement).value as 'auto' | 'light' | 'dark')"
        >
          <option value="auto">跟随系统</option>
          <option value="light">浅色模式</option>
          <option value="dark">深色模式</option>
        </select>
      </div>
      <div class="field">
        <label for="ui-pref-effects">渲染开销模式</label>
        <select
          id="ui-pref-effects"
          class="select"
          :value="ui.effects"
          @change="ui.setEffects(($event.target as HTMLSelectElement).value as 'glass' | 'lite')"
        >
          <option value="glass">标准模式</option>
          <option value="lite">轻量省电模式（禁用滤镜与图谱缩放过渡）</option>
        </select>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pref-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

@media (max-width: 768px) {
  .pref-grid {
    grid-template-columns: 1fr;
  }
}
</style>
