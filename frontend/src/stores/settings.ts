import { defineStore } from "pinia";
import { reactive, ref } from "vue";
import {
  manualSave,
  patchRuntimeConfig,
  setAutoSave as setAutoSaveApi,
  type ConfigPayload,
} from "@/services/configApi";
import { CONFIG_FIELDS, fieldValue } from "@/utils/configFields";
import { useAppStore } from "@/stores/app";
import { useDashboardStore } from "@/stores/dashboard";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";


/**
 * Settings store：配置表单状态 + 保存动作。
 *
 * 表单状态本地化（reactive form）：从 dashboard.config.config 初始化字段值，
 * 编辑时不直接写后端，保存时 collect updates → patchRuntimeConfig。
 *
 * 自检复用 useDashboardStore.runtime（同源 DRY），force 刷新调 dashboard.loadRuntime(true)。
 * config_persistence 后端从不返回 → persist 控件恒降级（与 legacy 一致），如实呈现。
 *
 * 修复 H7：UI 偏好（theme/effects）已在 P0 useUiStore 经 safeStorage 持久化，
 * 此处不再重复处理。错误显式进 useAppStore 总线（不静默吞错）。
 */
export const useSettingsStore = defineStore("settings", () => {
  /** 表单字段值（点分键 → number|boolean）。 */
  const form = reactive<Record<string, number | boolean>>({});
  /** 表单是否已从后端初始化。 */
  const initialized = ref(false);
  /** 最近一次完整配置（脱敏，用于显示 JSON + 重新初始化表单）。 */
  const rawConfig = ref<Record<string, unknown> | undefined>(undefined);
  const saving = ref(false);
  const autoSaveEnabled = ref(true);

  const app = useAppStore();
  const dashboard = useDashboardStore();
  const logs = useLogsStore();

  /** 从后端配置初始化表单（legacy renderConfigForm 的数据准备）。 */
  function initFromConfig(payload: ConfigPayload): void {
    rawConfig.value = payload.config;
    autoSaveEnabled.value = !!payload.auto_save_enabled;
    for (const group of CONFIG_FIELDS) {
      for (const field of group.fields) {
        form[field.key] = fieldValue(payload.config, field);
      }
    }
    initialized.value = true;
  }

  /** 收集表单更新（与 legacy collectConfigUpdates 行 3113-3126 一致）。 */
  function collectUpdates(): Record<string, unknown> {
    const updates: Record<string, unknown> = {};
    for (const group of CONFIG_FIELDS) {
      for (const field of group.fields) {
        const value = form[field.key];
        if (field.type === "boolean") {
          updates[field.key] = value === true;
        } else {
          const num = Number(value);
          if (Number.isFinite(num)) updates[field.key] = num;
        }
      }
    }
    return updates;
  }

  /** 保存到运行时（persist=false）。失败显式 toast，不静默。 */
  async function saveRuntime(): Promise<boolean> {
    saving.value = true;
    try {
      const result = await patchRuntimeConfig({ updates: collectUpdates(), persist: false });
      if (result.config) rawConfig.value = result.config;
      autoSaveEnabled.value = !!result.auto_save_enabled;
      logs.log("运行配置已更新", "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "saveRuntime");
      return false;
    } finally {
      saving.value = false;
    }
  }

  /** 写回配置文件（persist=true）。后端恒返回 persisted=false → 提示去插件配置页。 */
  async function persistConfig(): Promise<boolean> {
    saving.value = true;
    try {
      const result = await patchRuntimeConfig({ updates: collectUpdates(), persist: true });
      if (result.config) rawConfig.value = result.config;
      // 后端不持久化 → 显式告知用户，而非静默假装成功。
      app.pushError(result.persist_message || "当前 WebUI 不支持写回配置文件，请在 AstrBot 插件配置页持久化", "persistConfig");
      return result.persisted;
    } catch (err) {
      app.pushError(errText(err), "persistConfig");
      return false;
    } finally {
      saving.value = false;
    }
  }

  /** 开关自动保存。 */
  async function toggleAutoSave(enabled: boolean): Promise<boolean> {
    try {
      const result = await setAutoSaveApi(enabled);
      autoSaveEnabled.value = !!result.auto_save_enabled;
      logs.log(enabled ? "自动保存已开启" : "自动保存已关闭", "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "toggleAutoSave");
      return false;
    }
  }

  /** 手动保存所有 store 到磁盘。 */
  async function manualSaveAll(): Promise<boolean> {
    try {
      const result = await manualSave();
      logs.log(`手动保存完成：${result.saved?.join(", ") || "无"}`, "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "manualSave");
      return false;
    }
  }

  /** 从 dashboard.config 初始化（若 dashboard 已加载）。 */
  function ensureInitialized(): void {
    if (initialized.value) return;
    if (dashboard.config) initFromConfig(dashboard.config);
  }

  /** 强制刷新自检（复用 dashboard runtime，DRY）。 */
  function refreshSelfCheck(): Promise<unknown> {
    return dashboard.loadRuntime(true);
  }

  return {
    form,
    initialized,
    rawConfig,
    saving,
    autoSaveEnabled,
    initFromConfig,
    collectUpdates,
    saveRuntime,
    persistConfig,
    toggleAutoSave,
    manualSaveAll,
    ensureInitialized,
    refreshSelfCheck,
  };
});
