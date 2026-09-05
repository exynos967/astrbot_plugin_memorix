/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>;
  export default component;
}

// AstrBot 插件页桥接 SDK（由 dashboard 注入 page-bridge-sdk.js）。
interface AstrBotPluginPageBridge {
  ready(): Promise<void> | void;
  upload(route: string, file: File): Promise<unknown>;
  apiPost(route: string, payload: unknown): Promise<unknown>;
  apiGet?(route: string): Promise<unknown>;
}

interface Window {
  AstrBotPluginPage?: AstrBotPluginPageBridge;
}
