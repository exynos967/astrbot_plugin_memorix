import { defineStore } from "pinia";
import { computed, reactive, ref, watch } from "vue";
import {
  callRuntimeAdmin,
  manualSave,
  patchRuntimeConfig,
  setAutoSave as setAutoSaveApi,
  type ConfigPayload,
} from "@/services/configApi";
import {
  CONFIG_FIELDS,
  cloneFieldValue,
  fieldValue,
  isFieldValueEqual,
  isMaskedSecret,
  type ConfigFieldValue,
} from "@/utils/configFields";
import { useAppStore } from "@/stores/app";
import { useDashboardStore } from "@/stores/dashboard";
import { useGraphStore } from "@/stores/graph";
import { errText } from "@/utils/error";

/**
 * Settings store：v2.0 全量配置表单状态 + 脏字段检测 + 作用域联动 + 双池存储落盘。
 *
 * 关键保障：
 * 1. 监听 graph.effectiveScope() 切换，自动重载当前作用域的配置与自检报告。
 * 2. 维护 baseline 快照，实时计算 dirtyKeys / dirtyCount，支持一键放弃未应用修改。
 * 3. password 脱敏密钥保护：未改动或仍为掩码（<hidden> / ***）时绝不写入 PATCH 负载，防止覆盖真实密钥。
 * 4. persisted === false 时以温和提示条说明运行时已立即生效、永久配置由 AstrBot 插件配置页管理，不抛红框报错、不重复写日志。
 */
export const useSettingsStore = defineStore("settings", () => {
  const form = reactive<Record<string, ConfigFieldValue>>({});
  const baseline = reactive<Record<string, ConfigFieldValue>>({});
  const initialized = ref(false);
  const rawConfig = ref<Record<string, unknown> | undefined>(undefined);
  const saving = ref(false);
  const savingStorage = ref(false);
  const autoSaveEnabled = ref(true);
  const autoSaveInterval = ref(300);
  const persistNotice = ref("");

  const app = useAppStore();
  const dashboard = useDashboardStore();
  const graph = useGraphStore();

  let scopeLoadSeq = 0;

  function initFromConfig(payload: ConfigPayload): void {
    rawConfig.value = payload.config;
    autoSaveEnabled.value = !!payload.auto_save_enabled;
    if (typeof payload.auto_save_interval === "number" && payload.auto_save_interval > 0) {
      autoSaveInterval.value = payload.auto_save_interval;
    }
    for (const group of CONFIG_FIELDS) {
      for (const field of group.fields) {
        const val = fieldValue(payload.config, field);
        form[field.key] = cloneFieldValue(val);
        baseline[field.key] = cloneFieldValue(val);
      }
    }
    initialized.value = true;
  }

  const dirtyKeys = computed<string[]>(() => {
    if (!initialized.value) return [];
    const changed: string[] = [];
    for (const group of CONFIG_FIELDS) {
      for (const field of group.fields) {
        if (!isFieldValueEqual(form[field.key], baseline[field.key])) {
          changed.push(field.key);
        }
      }
    }
    return changed;
  });

  const dirtyCount = computed(() => dirtyKeys.value.length);

  function isFieldDirty(key: string): boolean {
    if (!initialized.value) return false;
    return !isFieldValueEqual(form[key], baseline[key]);
  }

  function discardChanges(): void {
    for (const group of CONFIG_FIELDS) {
      for (const field of group.fields) {
        if (field.key in baseline) {
          form[field.key] = cloneFieldValue(baseline[field.key]);
        }
      }
    }
    persistNotice.value = "";
  }

  /**
   * 仅收集相比 baseline 发生实质变更的字段，且严禁将未编辑的脱敏掩码写回后端。
   */
  function collectUpdates(): Record<string, unknown> {
    const updates: Record<string, unknown> = {};
    for (const group of CONFIG_FIELDS) {
      for (const field of group.fields) {
        const current = form[field.key];
        const base = baseline[field.key];
        if (isFieldValueEqual(current, base)) continue;

        if (field.type === "password") {
          const strVal = String(current ?? "");
          // 若仍为掩码字符串则跳过，绝不覆盖服务端真实密钥
          if (isMaskedSecret(strVal)) continue;
          updates[field.key] = strVal;
          continue;
        }

        if (field.type === "boolean") {
          updates[field.key] = current === true;
        } else if (field.type === "number") {
          const num = Number(current);
          if (Number.isFinite(num)) updates[field.key] = num;
        } else if (field.type === "list") {
          updates[field.key] = Array.isArray(current) ? [...current] : [];
        } else {
          updates[field.key] = String(current ?? "");
        }
      }
    }
    return updates;
  }

  async function saveRuntime(): Promise<boolean> {
    const updates = collectUpdates();
    const changeCount = Object.keys(updates).length;
    if (changeCount === 0) {
      app.notify("当前没有待应用的配置修改", "info", "运行配置");
      return true;
    }

    saving.value = true;
    const scope = graph.effectiveScope();
    try {
      const result = await patchRuntimeConfig({ updates, persist: false }, scope);
      if (scope !== graph.effectiveScope()) return true;
      initFromConfig({
        auto_save_enabled: result.auto_save_enabled,
        auto_save_interval: result.auto_save_interval,
        config: result.config ?? rawConfig.value,
      });
      persistNotice.value = "";
      app.pushSuccess(`已将 ${changeCount} 项配置应用到当前作用域运行时`, "运行配置");
      return true;
    } catch (err) {
      app.pushError(errText(err), "运行配置");
      return false;
    } finally {
      saving.value = false;
    }
  }

  async function persistConfig(): Promise<boolean> {
    const updates = collectUpdates();
    saving.value = true;
    const scope = graph.effectiveScope();
    try {
      const result = await patchRuntimeConfig({ updates, persist: true }, scope);
      if (scope !== graph.effectiveScope()) return true;
      initFromConfig({
        auto_save_enabled: result.auto_save_enabled,
        auto_save_interval: result.auto_save_interval,
        config: result.config ?? rawConfig.value,
      });

      if (result.persisted) {
        persistNotice.value = "";
        app.pushSuccess("配置已应用到运行时并永久写入配置文件", "运行配置");
        return true;
      }

      // 宿主不支持直接从 WebUI 改写插件配置文件时，温和提示运行时已生效，不抛红框错误
      const calmMsg =
        result.persist_message ||
        "配置已在当前作用域运行时立即生效；如需跨重启永久保存，请在 AstrBot「插件管理 → 配置」弹窗中修改。";
      persistNotice.value = calmMsg;
      app.notify(calmMsg, "info", "运行配置");
      return false;
    } catch (err) {
      app.pushError(errText(err), "运行配置");
      return false;
    } finally {
      saving.value = false;
    }
  }

  async function toggleAutoSave(enabled: boolean): Promise<boolean> {
    savingStorage.value = true;
    const scope = graph.effectiveScope();
    try {
      const result = await setAutoSaveApi(enabled, scope);
      if (scope !== graph.effectiveScope()) return true;
      autoSaveEnabled.value = !!result.auto_save_enabled;
      if (typeof result.auto_save_interval === "number" && result.auto_save_interval > 0) {
        autoSaveInterval.value = result.auto_save_interval;
      }
      app.pushSuccess(enabled ? "已开启定时自动落盘" : "已关闭定时自动落盘", "存储落盘");
      return true;
    } catch (err) {
      app.pushError(errText(err), "存储落盘");
      return false;
    } finally {
      savingStorage.value = false;
    }
  }

  /**
   * 立即落盘双池向量与知识图谱：优先走 /v1/admin/memory_runtime_admin (action: "save") 保存全部双池与元数据，
   * 若管理端点不可用则平滑回退 /api/save。
   */
  async function manualSaveAll(): Promise<boolean> {
    savingStorage.value = true;
    const scope = graph.effectiveScope();
    try {
      try {
        const res = await callRuntimeAdmin("save", {}, scope);
        if (scope !== graph.effectiveScope()) return true;
        const targets = Array.isArray(res.saved) && res.saved.length
          ? res.saved.join(", ")
          : "双池向量索引、知识图谱与元数据";
        app.pushSuccess(`落盘完成：${targets}`, "存储落盘");
        return true;
      } catch {
        const fallback = await manualSave(scope);
        if (scope !== graph.effectiveScope()) return true;
        app.pushSuccess(`落盘完成：${fallback.saved?.join(", ") || "向量与图谱"}`, "存储落盘");
        return true;
      }
    } catch (err) {
      app.pushError(errText(err), "存储落盘");
      return false;
    } finally {
      savingStorage.value = false;
    }
  }

  function ensureInitialized(): void {
    if (initialized.value) return;
    if (dashboard.config) initFromConfig(dashboard.config);
  }

  async function reloadForActiveScope(): Promise<void> {
    const seq = ++scopeLoadSeq;
    persistNotice.value = "";
    const [cfg] = await Promise.all([
      dashboard.loadConfig(),
      dashboard.loadRuntime(false),
    ]);
    if (seq !== scopeLoadSeq) return;
    if (cfg) {
      initFromConfig(cfg);
    } else if (dashboard.config) {
      initFromConfig(dashboard.config);
    }
  }

  function refreshSelfCheck(): Promise<unknown> {
    return dashboard.loadRuntime(true);
  }

  // 当 dashboard 异步拉取完配置且当前表单未处于脏编辑态时同步最新快照
  watch(
    () => dashboard.config,
    (cfg) => {
      if (cfg && (!initialized.value || dirtyCount.value === 0)) {
        initFromConfig(cfg);
      }
    },
  );

  // 监听记忆作用域切换，立即重载新作用域的配置与运行时自检状态
  watch(
    () => graph.effectiveScope(),
    (next, prev) => {
      if (next === prev) return;
      initialized.value = false;
      void reloadForActiveScope();
    },
  );

  return {
    form,
    baseline,
    initialized,
    rawConfig,
    saving,
    savingStorage,
    autoSaveEnabled,
    autoSaveInterval,
    persistNotice,
    dirtyKeys,
    dirtyCount,
    isFieldDirty,
    discardChanges,
    initFromConfig,
    collectUpdates,
    saveRuntime,
    persistConfig,
    toggleAutoSave,
    manualSaveAll,
    ensureInitialized,
    reloadForActiveScope,
    refreshSelfCheck,
  };
});
