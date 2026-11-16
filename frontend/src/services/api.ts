// AstrBot 插件页请求与文件传输桥：
// - 普通 API 请求走 window.AstrBotPluginPage.apiPost("webui/request", {method,url,data})
// - 文件上传统一先通过 POST /v1/upload-tickets 申请无查询串凭据，再调用 bridge.upload("webui/upload/" + ticket_id, file)
//   （严禁在 bridge.upload 端点中携带 ? 或 #，兼容 AstrBot normalizePluginEndpoint 校验）
// - 知识包下载优先走 bridge.download("webui/bundle-download", ...)，旧宿主自动降级分块 base64 流式重组。

import { errText } from "@/utils/error";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  /** 显式 scope；未传时由调用方决定（统一从 useGraphStore.effectiveScope 取）。 */
  scope?: string;
}

export class ApiError extends Error {
  constructor(message: string, readonly url: string, readonly method: HttpMethod) {
    super(message);
    this.name = "ApiError";
  }
}

interface UploadTicketResponse {
  success?: boolean;
  ticket_id?: string;
  error?: string;
}

interface BundleReadChunkResponse {
  success?: boolean;
  error?: string;
  chunk_base64?: string;
  offset?: number;
  next_offset?: number;
  size?: number;
  total_size?: number;
  eof?: boolean;
  has_more?: boolean;
}

const BUNDLE_CHUNK_BYTES = 4 * 1024 * 1024;

function resolveBridge(): NonNullable<Window["AstrBotPluginPage"]> {
  const bridge = window.AstrBotPluginPage;
  if (!bridge || typeof bridge.apiPost !== "function") {
    throw new ApiError("AstrBot 插件页桥接 SDK 未就绪", "bridge", "GET");
  }
  return bridge;
}

/** 拼接 scope query 参数（保持与后端一致的 `_scope` 键）。 */
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

async function uploadWithTicket<T>(
  file: File,
  ticketBody: Record<string, unknown>,
  scope?: string,
): Promise<T> {
  const bridge = resolveBridge();
  if (typeof bridge.ready === "function") {
    await bridge.ready();
  }
  if (typeof bridge.upload !== "function") {
    throw new ApiError("当前 AstrBot 宿主不支持文件上传桥接", "webui/upload", "POST");
  }

  const ticket = await request<UploadTicketResponse>("POST", "/v1/upload-tickets", ticketBody, { scope });
  if (!ticket || ticket.success === false || !ticket.ticket_id) {
    throw new ApiError(ticket?.error || "获取文件上传凭据失败", "/v1/upload-tickets", "POST");
  }

  // 严禁拼接 ? 或 #，直接将纯路径段传给 bridge.upload
  const endpoint = "webui/upload/" + encodeURIComponent(ticket.ticket_id);
  let envelope: unknown;
  try {
    envelope = await bridge.upload(endpoint, file);
  } catch (err) {
    throw new ApiError(errText(err), endpoint, "POST");
  }
  return unwrapEnvelope<T>(envelope, endpoint, "POST");
}

export async function uploadImport<T>(file: File, options: Record<string, unknown>, scope: string): Promise<T> {
  const limits = await request<{ max_file_size_bytes: number; suffixes: string[] }>(
    "GET",
    "/v1/import/limits",
    undefined,
    { scope },
  );
  if (file.size > limits.max_file_size_bytes) {
    throw new ApiError(
      `文件超过 ${Math.floor(limits.max_file_size_bytes / 1048576)} MB 上限`,
      "webui/upload",
      "POST",
    );
  }
  const dotIdx = file.name.lastIndexOf(".");
  const suffix = dotIdx >= 0 ? file.name.slice(dotIdx).toLowerCase() : "";
  if (!suffix || !limits.suffixes.includes(suffix)) {
    throw new ApiError(`不支持此文件类型（支持：${limits.suffixes.join(", ")}）`, "webui/upload", "POST");
  }
  return uploadWithTicket<T>(file, { kind: "import", options }, scope);
}

export async function uploadBundleFile<T = unknown>(file: File, scope?: string): Promise<T> {
  if (!file.name.toLowerCase().endsWith(".amembundle")) {
    throw new ApiError("仅支持上传 .amembundle 知识包文件", "webui/upload", "POST");
  }
  return uploadWithTicket<T>(file, { kind: "bundle" }, scope);
}

function decodeBase64Bytes(b64: string): Uint8Array {
  const binary = window.atob(b64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

export async function downloadBundleFile(fileName: string, scope?: string): Promise<void> {
  if (!fileName) {
    throw new ApiError("缺少要下载的知识包文件名", "webui/bundle-download", "GET");
  }
  const bridge = resolveBridge();
  if (typeof bridge.ready === "function") {
    await bridge.ready();
  }

  if (typeof bridge.download === "function") {
    const params: Record<string, unknown> = scope
      ? { _scope: scope, file_name: fileName }
      : { file_name: fileName };
    try {
      await bridge.download("webui/bundle-download", params, fileName);
      return;
    } catch (err) {
      throw new ApiError(errText(err), "webui/bundle-download", "GET");
    }
  }

  // 宿主不支持 bridge.download 时，走 4MB 分块 base64 下载兜底
  const chunks: BlobPart[] = [];
  let offset = 0;
  for (let guard = 0; guard < 2048; guard += 1) {
    const res = await api.post<BundleReadChunkResponse>(
      "/v1/bundles",
      {
        action: "read_chunk",
        payload: { file_name: fileName, offset, chunk_size: BUNDLE_CHUNK_BYTES },
      },
      { scope },
    );
    if (!res || res.success === false) {
      throw new ApiError(res?.error || "分块读取知识包失败", "/v1/bundles", "POST");
    }
    const b64 = res.chunk_base64 || "";
    if (!b64) break;

    const bytes = decodeBase64Bytes(b64);
    if (bytes.byteLength === 0) break;
    chunks.push(bytes.buffer as ArrayBuffer);
    offset = typeof res.next_offset === "number" ? res.next_offset : offset + bytes.byteLength;

    const totalSize = typeof res.total_size === "number" ? res.total_size : res.size;
    if (res.eof === true || res.has_more === false) break;
    if (typeof totalSize === "number" && offset >= totalSize) break;
    if (
      res.eof === undefined &&
      res.has_more === undefined &&
      totalSize === undefined &&
      bytes.byteLength < BUNDLE_CHUNK_BYTES
    ) {
      break;
    }
  }

  const blob = new Blob(chunks, { type: "application/octet-stream" });
  const blobUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = blobUrl;
  anchor.download = fileName;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 10000);
}

/** 安全复制文本：优先 Clipboard API，受限 iframe 下自动回退隐藏 textarea + execCommand。 */
export async function copyTextSafe(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 权限受限时继续走 execCommand 兜底
    }
  }
  if (typeof document === "undefined") return false;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.top = "-9999px";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  try {
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    return Boolean(document.execCommand("copy"));
  } catch {
    return false;
  } finally {
    document.body.removeChild(textarea);
  }
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
  return envelope as T;
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
