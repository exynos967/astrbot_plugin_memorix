// AstrBot 插件页请求桥：复用 legacy request() 逻辑（legacy index.html 行 2688-2704），
// 走 window.AstrBotPluginPage.apiPost("webui/request", {method,url,data})。
//
// C5 修复点（P2 接入）：legacy 中 loadStats 用全局 scope、loadGraph 用图谱页下拉 scope，
// 两者写入同一 DOM 导致节点总量不一致。新实现统一从 useGraphStore.currentScope 读取 scope，
// 由调用方经 options.scope 传入；本骨架仅透传，scope 联动刷新在 P2 useScope composable 完成。

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  /** 显式 scope；未传时由调用方决定（P2 起统一从 useGraphStore.currentScope 取）。 */
  scope?: string;
}

export class ApiError extends Error {
  constructor(message: string, readonly url: string, readonly method: HttpMethod) {
    super(message);
    this.name = "ApiError";
  }
}

function resolveBridge(): NonNullable<Window["AstrBotPluginPage"]> {
  const bridge = window.AstrBotPluginPage;
  if (!bridge || typeof bridge.apiPost !== "function") {
    throw new ApiError("AstrBot 插件页桥接 SDK 未就绪", "bridge", "GET");
  }
  return bridge;
}

/** 拼接 scope query 参数（保持与 legacy 一致的 `_scope` 键）。 */
function withScope(url: string, scope?: string): string {
  if (!scope) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}_scope=${encodeURIComponent(scope)}`;
}

/**
 * 调用后端 API。method/url 走 AstrBot webui/request 桥。
 * 返回 envelope.data（status=ok）或抛 ApiError（status=error / 桥异常）。
 */
export async function request<T = unknown>(
  method: HttpMethod,
  url: string,
  data?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const bridge = resolveBridge();
  if (typeof bridge.ready === "function") {
    await bridge.ready();
  }
  const finalUrl = withScope(url, options.scope);
  let envelope: unknown;
  try {
    envelope = await bridge.apiPost("webui/request", { method, url: finalUrl, data });
  } catch (err) {
    throw new ApiError(errText(err), finalUrl, method);
  }
  return unwrapEnvelope<T>(envelope, finalUrl, method);
}

function unwrapEnvelope<T>(envelope: unknown, url: string, method: HttpMethod): T {
  if (envelope && typeof envelope === "object") {
    const env = envelope as { status?: string; data?: unknown; message?: string };
    if (env.status === "error") {
      throw new ApiError(env.message || "请求失败", url, method);
    }
    if (env.status === "ok" && "data" in env) {
      return env.data as T;
    }
  }
  // 非标准 envelope，原样返回。
  return envelope as T;
}

function errText(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err ?? "未知错误");
}

/** 便捷方法。 */
export const api = {
  get: <T = unknown>(url: string, options?: RequestOptions) => request<T>("GET", url, undefined, options),
  post: <T = unknown>(url: string, data?: unknown, options?: RequestOptions) =>
    request<T>("POST", url, data, options),
  put: <T = unknown>(url: string, data?: unknown, options?: RequestOptions) =>
    request<T>("PUT", url, data, options),
  patch: <T = unknown>(url: string, data?: unknown, options?: RequestOptions) =>
    request<T>("PATCH", url, data, options),
  delete: <T = unknown>(url: string, data?: unknown, options?: RequestOptions) =>
    request<T>("DELETE", url, data, options),
};
