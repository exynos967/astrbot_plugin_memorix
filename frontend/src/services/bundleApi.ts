// 知识包迁移服务层（.amembundle）：对接 POST /v1/bundles 与无查询串凭据上传/下载桥。
import { ApiError, api, downloadBundleFile, uploadBundleFile } from "@/services/api";

export type BundleContentLevel = "knowledge" | "full";
export type BundleSelectorType = "all" | "chat" | "source" | "package" | "import_task" | "image";

export interface BundlePackageMeta {
  id?: string;
  name: string;
  version: string;
  description: string;
  author: string;
}

export interface BundleSelectorPayload {
  type: BundleSelectorType;
  chat_id?: string;
  values?: string[];
  installation_id?: string;
  package_id?: string;
  task_id?: string;
  content_hashes?: string[];
  occurrence_ids?: string[];
}

export interface BundleExportRequest {
  preview: boolean;
  content_level: BundleContentLevel;
  include_vectors: boolean;
  include_images: boolean;
  include_image_related: boolean;
  package: BundlePackageMeta;
  selector: BundleSelectorPayload;
}

export interface BundleExportResponse {
  success?: boolean;
  error?: string;
  preview?: boolean;
  file_name?: string;
  path?: string;
  manifest?: Record<string, unknown>;
  counts?: Record<string, number>;
  size?: number;
  uncompressed_size?: number;
}

export interface BundleInspectImagesInfo {
  vector_compatible?: boolean;
  has_vectors?: boolean;
  runtime_status?: string;
  missing_resources?: string[];
  asset_count?: number;
}

export interface BundleInspectResponse {
  success?: boolean;
  error?: string;
  path?: string;
  file_name?: string;
  size?: number;
  content_level?: BundleContentLevel | string;
  package?: Partial<BundlePackageMeta>;
  manifest?: Record<string, unknown>;
  counts?: Record<string, number>;
  vector_compatible?: boolean;
  vectors?: {
    compatible?: boolean;
    has_vectors?: boolean;
    reason?: string;
  };
  images?: BundleInspectImagesInfo;
}

export interface BundleImportRequest {
  path: string;
  scope_type: "global" | "chat";
  chat_id?: string;
  mode: "merge" | "restore";
}

export interface BundleImportResponse {
  success?: boolean;
  error?: string;
  already_installed?: boolean;
  installation_id?: string;
  package?: Partial<BundlePackageMeta>;
  content_level?: string;
  mode?: string;
  scope_type?: string;
  chat_id?: string;
  inserted?: Record<string, number> | number;
  mapped_paragraphs?: number;
  vectors?: Record<string, unknown>;
  graph?: Record<string, unknown>;
  images?: Record<string, unknown>;
}

export interface InstalledBundleItem {
  installation_id: string;
  package_id: string;
  version: string;
  name: string;
  content_level: string;
  content_digest?: string;
  scope_type?: string;
  scope_key?: string;
  chat_id?: string;
  status?: string;
  installed_at?: number | string;
  updated_at?: number | string;
  paragraph_count?: number;
  images?: {
    assets?: number;
    occurrences?: number;
    ready?: number;
    content_status?: string;
    retrieval_status?: string;
  };
}

export interface BundleUninstallResponse {
  success?: boolean;
  error?: string;
  installation_id?: string;
  removed_memories?: number;
  removed?: Record<string, number>;
  retained_shared?: number | Record<string, number>;
  removed_vectors?: number;
  removed_images?: number | Record<string, number>;
  message?: string;
}

export interface BundleUploadResult {
  success?: boolean;
  error?: string;
  path?: string;
  saved_path?: string;
  file_path?: string;
  file_name?: string;
  size?: number;
  inspect?: BundleInspectResponse;
}

async function callBundles<T extends { success?: boolean; error?: string }>(
  action: string,
  payload: Record<string, unknown>,
  scope?: string,
): Promise<T> {
  const res = await api.post<T>("/v1/bundles", { action, payload }, { scope });
  if (res && res.success === false) {
    throw new ApiError(res.error || `知识包操作 ${action} 失败`, "/v1/bundles", "POST");
  }
  return res;
}

export const bundleApi = {
  listInstalled: (limit = 100, scope?: string) =>
    callBundles<{ success?: boolean; error?: string; items?: InstalledBundleItem[]; count?: number }>(
      "list",
      { limit },
      scope,
    ),

  exportBundle: (req: BundleExportRequest, scope?: string) =>
    callBundles<BundleExportResponse>("export", req as unknown as Record<string, unknown>, scope),

  inspectBundle: (path: string, scope?: string) =>
    callBundles<BundleInspectResponse>("inspect", { path }, scope),

  importBundle: (req: BundleImportRequest, scope?: string) =>
    callBundles<BundleImportResponse>("import", req as unknown as Record<string, unknown>, scope),

  uninstallBundle: (installationId: string, scope?: string) =>
    callBundles<BundleUninstallResponse>("uninstall", { installation_id: installationId }, scope),

  uploadFile: (file: File, scope?: string) => uploadBundleFile<BundleUploadResult>(file, scope),

  downloadFile: (fileName: string, scope?: string) => downloadBundleFile(fileName, scope),
};
