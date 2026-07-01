import { defineConfig, type Plugin } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

// AstrBot 插件页托管约束（plugin_page_service.py）：
// - normalize_plugin_page_path 拒绝以 "/" 开头的绝对路径 → 必须 base: './' 产出相对路径
// - 裸 @import（无 url() 包装）不被 rewrite → 禁用，CSS 用 JS import 注入
// - new URL(x, import.meta.url) 不被 rewrite → 禁用此模式
// - Cache-Control: no-store 全资源不缓存 → vis-network 单独 chunk 懒加载，控体积
//
// ⚠ AstrBot rewrite 正则缺陷（plugin_page_service.py _JS_MODULE_FROM_RE）：
//   正则要求 `import\s+`（import 后至少一空格），但 Vite/rollup minified 产物是
//   `import{a,b}from"./x.js"`（import 后零空格直接接 {）→ 正则不匹配 → chunk 间静态
//   import 不被 rewrite → 资源 URL 无 asset_token → AstrBot 鉴权 401。
//   只有真·动态 import 的 graph view 会产生跨 chunk 静态 import（GraphView→vis、GraphView→index），
//   故症状为"唯独图谱页空白，其余 9 view 正常"。
//   修复：astrbotEsmImportCompat 插件在 generateBundle 阶段给 minified 的 import{...}from /
//   export{...}from / side-effect import" 补回空格，让 AstrBot 正则认得，rewrite 注 token。
//   保留 minify 体积优势，只补几处关键空格。
function astrbotEsmImportCompat(): Plugin {
  return {
    name: "astrbot-esm-import-compat",
    // generateBundle 在 esbuild minify 之后，此时 chunk.code 已是 minified 最终产物
    generateBundle(_options, bundle) {
      for (const fileName of Object.keys(bundle)) {
        const chunk = bundle[fileName];
        if (chunk.type !== "chunk") continue;
        let code = chunk.code;
        if (!code) continue;
        // import{a,b}from"./x" → import {a,b} from "./x"（AstrBot 正则要求 import\s+ 与 from\s+）
        code = code.replace(/import\{([^}]*?)\}from"/g, 'import {$1} from "');
        code = code.replace(/export\{([^}]*?)\}from"/g, 'export {$1} from "');
        // side-effect import"./x" → import "./x"（仅相对路径，不误伤 import("./x") 动态调用）
        code = code.replace(/import"(?=\.\/)/g, 'import "');
        chunk.code = code;
      }
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [vue(), astrbotEsmImportCompat()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../pages/memorix",
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 8192,
    chunkSizeWarningLimit: 1200,
    // ⚠ AstrBot iframe sandbox 不带 allow-same-origin → iframe origin=null → 子 chunk
    // （动态 import / modulepreload / CSS chunk）请求被浏览器 CORS 拦截（ACAO:* 对 origin=null
    // 不生效），唯独 graph 页依赖动态 import vis/GraphView 子 chunk → 加载失败画布空。
    // 根治：禁用代码分割，所有 JS+CSS 内联进单 bundle，HTML <script>/<link> 同源直接加载，
    // 不走 fetch CORS。代价：主 bundle 含 vis（~500KB），首屏略慢，但 AstrBot no-store 反正重载。
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        // 单 bundle：禁用 manualChunks + 内联所有动态 import，杜绝子 chunk 跨域加载
        inlineDynamicImports: true,
      },
    },
  },
  server: {
    // dev 仅组件级开发；集成调试走 build 产物经 AstrBot 托管（iframe 同源约束）。
    port: 5174,
    strictPort: true,
  },
});
