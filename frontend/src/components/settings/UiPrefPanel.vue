<script setup lang="ts">
// 界面偏好面板：主题 + 视觉效果，双向绑定 useUiStore（已持久化，修 H7）。
// 从 legacy view-settings 界面偏好（index.html 行 2355-2378）迁移。
// storage 可用性经 utils/storage.ts safeStorage 探测，实时反映降级状态。
import { computed } from "vue";
import { useUiStore } from "@/stores/ui";
import { realStorageAvailable } from "@/utils/storage";

const ui = useUiStore();

const storageOk = computed(() => realStorageAvailable());
const statusText = computed(() => (storageOk.value ? "本地保存" : "本页会话有效"));
</script>

<template>
  <div class="band" style="margin-top: 0">
    <div class="panel-title">
      <h2>界面偏好</h2>
      <span class="section-label">{{ statusText }}</span>
    </div>
    <div class="settings-fields">
      <div class="field">
        <label>主题模式</label>
        <select class="select" :value="ui.theme" @change="ui.setTheme(($event.target as HTMLSelectElement).value as 'auto' | 'light' | 'dark')">
          <option value="auto">跟随系统</option>
          <option value="light">浅色</option>
          <option value="dark">深色</option>
        </select>
      </div>
      <div class="field">
        <label>视觉效果</label>
        <select class="select" :value="ui.effects" @change="ui.setEffects(($event.target as HTMLSelectElement).value as 'glass' | 'lite')">
          <option value="glass">完整毛玻璃</option>
          <option value="lite">轻量模式</option>
        </select>
      </div>
    </div>
    <p class="settings-note">此偏好仅保存在当前浏览器本地，不写入 AstrBot 服务配置；如果插件页被浏览器沙盒限制 localStorage，则仅在本页会话内生效。轻量模式会关闭毛玻璃滤镜与背景动效；图谱性能模式下会自动禁用初始化缩放动画。</p>
  </div>
</template>
