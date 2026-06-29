import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

// AstrBot 插件页托管约束（plugin_page_service.py）：
// - normalize_plugin_page_path 拒绝以 "/" 开头的绝对路径 → 必须 base: './' 产出相对路径
// - 裸 @import（无 url() 包装）不被 rewrite → 禁用，CSS 用 JS import 注入
// - new URL(x, import.meta.url) 不被 rewrite → 禁用此模式
// - Cache-Control: no-store 全资源不缓存 → vis-network 单独 chunk 懒加载，控体积
export default defineConfig({
  base: "./",
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../pages/memorix-vue",
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 8192,
    chunkSizeWarningLimit: 1200,
    cssCodeSplit: true,
    // vis-network 单独 chunk 懒加载：GraphView 动态 import vis-network，
    // 配合 manualChunks 把 vis-network + vis-data 归到 vis chunk 控制体积（首屏不加载）。
    rollupOptions: {
      output: {
        manualChunks: { vis: ["vis-network", "vis-data"] },
      },
    },
  },
  server: {
    // dev 仅组件级开发；集成调试走 build 产物经 AstrBot 托管（iframe 同源约束）。
    port: 5174,
    strictPort: true,
  },
});
