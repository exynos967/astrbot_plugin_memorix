import { defineStore } from "pinia";
import { computed, reactive, ref, watch } from "vue";
import {
  bundleApi,
  type BundleContentLevel,
  type BundleExportResponse,
  type BundleInspectResponse,
  type BundleSelectorPayload,
  type BundleSelectorType,
  type InstalledBundleItem,
} from "@/services/bundleApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { errText } from "@/utils/error";

function splitCsvValues(raw: string): string[] {
  return raw
    .split(/[\r\n,，]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export const useBundlesStore = defineStore("bundles", () => {
  const graph = useGraphStore();
  const app = useAppStore();

  const scope = computed(() => graph.effectiveScope());
  let installedSeq = 0;

  // 左侧：导出表单状态
  const exportForm = reactive({
    packageId: "",
    name: "memorix-knowledge-pack",
    version: "1.0.0",
    author: "",
    description: "",
    contentLevel: "knowledge" as BundleContentLevel,
    includeVectors: true,
    includeImages: true,
    includeImageRelated: true,
    selectorType: "all" as BundleSelectorType,
    selectorChatId: "",
    selectorSourcesText: "",
    selectorInstallationId: "",
    selectorPackageId: "",
    selectorTaskId: "",
    selectorImageHashesText: "",
  });

  const previewLoading = ref(false);
  const exporting = ref(false);
  const previewData = ref<BundleExportResponse | null>(null);
  const lastExportedFile = ref("");

  // 右侧上方：上传 + 预检 + 导入状态
  const uploading = ref(false);
  const uploadedPath = ref("");
  const uploadedFileName = ref("");
  const inspectResult = ref<BundleInspectResponse | null>(null);
  const importForm = reactive({
    scopeType: "global" as "global" | "chat",
    chatId: "",
    mode: "merge" as "merge" | "restore",
  });
  const importing = ref(false);

  // 右侧下方：已安装包列表
  const installedLoading = ref(false);
  const installedItems = ref<InstalledBundleItem[]>([]);
  const uninstallingId = ref("");

  function validateSelector(): string | null {
    const t = exportForm.selectorType;
    if (t === "chat" && !exportForm.selectorChatId.trim()) {
      return "按会话导出时请填写目标会话 Chat ID";
    }
    if (t === "source" && splitCsvValues(exportForm.selectorSourcesText).length === 0) {
      return "按来源导出时请至少填写一个来源标识";
    }
    if (
      t === "package" &&
      !exportForm.selectorInstallationId.trim() &&
      !exportForm.selectorPackageId.trim()
    ) {
      return "按已安装包导出时请选择安装记录或填写 Package ID";
    }
    if (t === "import_task" && !exportForm.selectorTaskId.trim()) {
      return "按导入任务导出时请填写导入任务 ID";
    }
    if (t === "image" && splitCsvValues(exportForm.selectorImageHashesText).length === 0) {
      return "按图片哈希导出时请至少填写一个图片哈希";
    }
    return null;
  }

  function buildSelectorPayload(): BundleSelectorPayload {
    const t = exportForm.selectorType;
    if (t === "chat") {
      return { type: "chat", chat_id: exportForm.selectorChatId.trim() };
    }
    if (t === "source") {
      return { type: "source", values: splitCsvValues(exportForm.selectorSourcesText) };
    }
    if (t === "package") {
      return {
        type: "package",
        ...(exportForm.selectorInstallationId.trim()
          ? { installation_id: exportForm.selectorInstallationId.trim() }
          : {}),
        ...(exportForm.selectorPackageId.trim()
          ? { package_id: exportForm.selectorPackageId.trim() }
          : {}),
      };
    }
    if (t === "import_task") {
      return { type: "import_task", task_id: exportForm.selectorTaskId.trim() };
    }
    if (t === "image") {
      return {
        type: "image",
        content_hashes: splitCsvValues(exportForm.selectorImageHashesText),
      };
    }
    return { type: "all" };
  }

  function buildExportPayload(preview: boolean) {
    return {
      preview,
      content_level: exportForm.contentLevel,
      include_vectors: exportForm.includeVectors,
      include_images: exportForm.includeImages,
      include_image_related: exportForm.includeImageRelated,
      package: {
        ...(exportForm.packageId.trim() ? { id: exportForm.packageId.trim() } : {}),
        name: exportForm.name.trim() || "memorix-knowledge-pack",
        version: exportForm.version.trim() || "1.0.0",
        author: exportForm.author.trim(),
        description: exportForm.description.trim(),
      },
      selector: buildSelectorPayload(),
    };
  }

  async function runExportPreview(): Promise<void> {
    const validationErr = validateSelector();
    if (validationErr) {
      app.pushError(validationErr, "预览知识包");
      return;
    }
    const reqScope = scope.value;
    previewLoading.value = true;
    try {
      const res = await bundleApi.exportBundle(buildExportPayload(true), reqScope);
      if (reqScope === scope.value) {
        previewData.value = res;
      }
    } catch (err) {
      app.pushError(errText(err), "预览知识包");
    } finally {
      previewLoading.value = false;
    }
  }

  async function exportAndDownload(): Promise<void> {
    const validationErr = validateSelector();
    if (validationErr) {
      app.pushError(validationErr, "导出知识包");
      return;
    }
    const reqScope = scope.value;
    exporting.value = true;
    try {
      const res = await bundleApi.exportBundle(buildExportPayload(false), reqScope);
      if (reqScope !== scope.value) return;
      previewData.value = res;
      const fileName = res.file_name || (res.path ? res.path.split(/[\\/]/).pop() || "" : "");
      if (!fileName) {
        app.pushError("导出已完成，但未返回可下载文件名", "导出知识包");
        return;
      }
      lastExportedFile.value = fileName;
      await bundleApi.downloadFile(fileName, reqScope);
      app.pushSuccess(`知识包 ${fileName} 已生成并触发下载`, "导出知识包");
    } catch (err) {
      app.pushError(errText(err), "导出知识包");
    } finally {
      exporting.value = false;
    }
  }

  async function redownloadLast(): Promise<void> {
    if (!lastExportedFile.value) return;
    exporting.value = true;
    try {
      await bundleApi.downloadFile(lastExportedFile.value, scope.value);
      app.pushSuccess(`已重新下载 ${lastExportedFile.value}`, "导出知识包");
    } catch (err) {
      app.pushError(errText(err), "下载知识包");
    } finally {
      exporting.value = false;
    }
  }

  async function uploadAndInspect(file: File): Promise<void> {
    const reqScope = scope.value;
    uploading.value = true;
    try {
      const uploaded = await bundleApi.uploadFile(file, reqScope);
      if (uploaded?.success === false) {
        throw new Error(uploaded.error || "知识包上传失败");
      }
      const serverPath =
        uploaded.path || uploaded.saved_path || uploaded.file_path || uploaded.file_name || "";
      if (!serverPath) {
        throw new Error("服务端未返回上传后的知识包路径");
      }
      if (reqScope !== scope.value) return;
      uploadedPath.value = serverPath;
      uploadedFileName.value = uploaded.file_name || file.name;

      const inspected =
        uploaded.inspect && uploaded.inspect.success !== false
          ? uploaded.inspect
          : await bundleApi.inspectBundle(serverPath, reqScope);
      if (reqScope !== scope.value) return;
      inspectResult.value = {
        ...inspected,
        path: serverPath,
        file_name: inspected.file_name || uploadedFileName.value,
      };
      app.pushSuccess(`已完成 ${file.name} 兼容性预检`, "知识包预检");
    } catch (err) {
      app.pushError(errText(err), "知识包预检");
    } finally {
      uploading.value = false;
    }
  }

  function clearInspection(): void {
    uploadedPath.value = "";
    uploadedFileName.value = "";
    inspectResult.value = null;
  }

  async function confirmImportBundle(): Promise<void> {
    if (!uploadedPath.value) return;
    if (importForm.scopeType === "chat" && !importForm.chatId.trim()) {
      app.pushError("选择会话级导入时请填写目标会话 Chat ID", "导入知识包");
      return;
    }

    if (importForm.mode === "restore") {
      const ok = await app.requestConfirmation({
        title: "以还原模式导入知识包",
        message:
          "还原模式适用于按包完整恢复，可能覆盖同源条目状态。确定继续导入该知识包吗？",
        confirmText: "还原导入",
        danger: true,
      });
      if (!ok) return;
    }

    const reqScope = scope.value;
    importing.value = true;
    try {
      const res = await bundleApi.importBundle(
        {
          path: uploadedPath.value,
          scope_type: importForm.scopeType,
          ...(importForm.scopeType === "chat" ? { chat_id: importForm.chatId.trim() } : {}),
          mode: importForm.mode,
        },
        reqScope,
      );
      if (reqScope !== scope.value) return;
      if (res.already_installed) {
        app.notify("该版本知识包已安装，无需重复写入", "info", "导入知识包");
      } else {
        app.pushSuccess(
          `知识包导入成功（安装 ID：${res.installation_id || "-"}）`,
          "导入知识包",
        );
      }
      clearInspection();
      await loadInstalled();
    } catch (err) {
      app.pushError(errText(err), "导入知识包");
    } finally {
      importing.value = false;
    }
  }

  async function loadInstalled(): Promise<void> {
    const seq = ++installedSeq;
    const reqScope = scope.value;
    installedLoading.value = true;
    try {
      const res = await bundleApi.listInstalled(100, reqScope);
      if (seq !== installedSeq || reqScope !== scope.value) return;
      installedItems.value = Array.isArray(res.items) ? res.items : [];
    } catch (err) {
      if (seq === installedSeq && reqScope === scope.value) {
        app.pushError(errText(err), "已安装包列表");
      }
    } finally {
      if (seq === installedSeq) installedLoading.value = false;
    }
  }

  async function uninstallPackage(item: InstalledBundleItem): Promise<void> {
    const confirmed = await app.requestConfirmation({
      title: `卸载知识包「${item.name || item.package_id}」`,
      message: `确定卸载 ${item.package_id} (${item.version}) 吗？系统仅移除该知识包独占的记忆、向量与图片出现记录；已被其他对话或来源共享引用的记忆将自动保留。`,
      confirmText: "卸载知识包",
      danger: true,
    });
    if (!confirmed) return;

    const reqScope = scope.value;
    uninstallingId.value = item.installation_id;
    try {
      const res = await bundleApi.uninstallBundle(item.installation_id, reqScope);
      if (reqScope !== scope.value) return;
      app.pushSuccess(
        res.message || `已卸载知识包 ${item.name || item.package_id}（移除独占记忆 ${res.removed_memories ?? 0} 条）`,
        "卸载知识包",
      );
      await loadInstalled();
    } catch (err) {
      app.pushError(errText(err), "卸载知识包");
    } finally {
      uninstallingId.value = "";
    }
  }

  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (next === prev) return;
      installedSeq += 1;
      previewData.value = null;
      lastExportedFile.value = "";
      clearInspection();
      void loadInstalled();
    },
  );

  return {
    exportForm,
    previewLoading,
    exporting,
    previewData,
    lastExportedFile,
    uploading,
    uploadedPath,
    uploadedFileName,
    inspectResult,
    importForm,
    importing,
    installedLoading,
    installedItems,
    uninstallingId,
    runExportPreview,
    exportAndDownload,
    redownloadLast,
    uploadAndInspect,
    clearInspection,
    confirmImportBundle,
    loadInstalled,
    uninstallPackage,
  };
});
