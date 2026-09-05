import { defineStore } from "pinia";
import { ref, watch } from "vue";
import {
  clearPersonOverride as clearPersonOverrideApi,
  fetchPersonRegistry,
  queryPerson as queryPersonApi,
  savePersonOverride as savePersonOverrideApi,
  type PersonProfile,
  type PersonRegistryItem,
} from "@/services/personApi";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";


/**
 * People store：人物候选 + 画像查询 + 人工覆盖。
 *
 * 候选、画像和人工覆盖统一使用当前记忆作用域。
 *
 * 修复 H4（详情快速切换错位）：queryPerson / loadCandidates 各持单调请求序号，
 * await 后若序号已变（用户在返回前又发起新请求）则丢弃旧结果，避免旧画像/旧候选
 * 覆盖新结果导致错位。错误显式进 useAppStore 总线（不静默吞错）。
 */
export const usePeopleStore = defineStore("people", () => {
  const candidates = ref<PersonRegistryItem[]>([]);
  const profile = ref<PersonProfile | null>(null);
  const keyword = ref("");
  const topk = ref(12);
  const overrideId = ref("");
  const overrideText = ref("");
  const loadingCandidates = ref(false);
  const querying = ref(false);
  const busy = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  let candidateSeq = 0;
  let querySeq = 0;
  // 候选菜单（PersonQueryPanel/PersonOverridePanel）独立的只读序号，避免与右栏候选列表的 candidateSeq 互相 invalidate。
  let suggestSeq = 0;

  /**
   * 候选菜单只读建议：拉 registry 返回候选项，**不写共享 candidates 状态**、不进 candidateSeq。
   * 原候选菜单 source 复用 loadCandidates 会污染右栏 PersonListPanel 列表 + 两侧序号互相丢弃，
   * 此处独立路径隔离。仍持序号守卫丢弃过期请求。
   */
  async function suggestPersons(kw = ""): Promise<PersonRegistryItem[]> {
    const seq = ++suggestSeq;
    try {
      const data = await fetchPersonRegistry(kw, 1, 30, graph.effectiveScope());
      if (seq !== suggestSeq) return [];
      return data.items || [];
    } catch {
      if (seq === suggestSeq) return [];
      return [];
    }
  }

  /** 拉取候选 registry 写入右栏候选列表（"候选列表"按钮 + onMounted 用）。 */
  async function loadCandidates(kw = ""): Promise<PersonRegistryItem[]> {
    candidateSeq += 1;
    const seq = candidateSeq;
    loadingCandidates.value = true;
    try {
      const data = await fetchPersonRegistry(kw, 1, 30, graph.effectiveScope());
      if (seq !== candidateSeq) return []; // 旧请求丢弃（修 H4）
      candidates.value = data.items || [];
      return candidates.value;
    } catch (err) {
      if (seq === candidateSeq) {
        app.pushError(errText(err), "loadPersonSuggestions");
        candidates.value = [];
      }
      return [];
    } finally {
      if (seq === candidateSeq) loadingCandidates.value = false;
    }
  }

  /** "候选列表"按钮：按当前关键词刷新候选。 */
  async function refreshCandidates(): Promise<void> {
    await loadCandidates(keyword.value.trim());
  }

  /** 查询画像（H4：序号守卫丢弃过期结果）。 */
  async function query(): Promise<boolean> {
    const kw = keyword.value.trim();
    if (!kw) {
      app.pushError("请填写人物关键词", "queryPerson");
      return false;
    }
    querySeq += 1;
    const seq = querySeq;
    querying.value = true;
    try {
      const data = await queryPersonApi({
        person_keyword: kw,
        top_k: Number(topk.value) || 12,
        force_refresh: false,
      }, graph.effectiveScope());
      if (seq !== querySeq) return false; // 旧请求丢弃（修 H4 错位）
      profile.value = data;
      overrideId.value = data.person_id || overrideId.value || kw;
      overrideText.value = data.manual_override_text || data.override_text || "";
      logs.log(`人物画像查询：${data.person_name || kw}`, "info");
      return true;
    } catch (err) {
      if (seq === querySeq) app.pushError(errText(err), "queryPerson");
      return false;
    } finally {
      if (seq === querySeq) querying.value = false;
    }
  }

  async function saveOverride(): Promise<boolean> {
    const id = overrideId.value.trim();
    const text = overrideText.value.trim();
    if (!id || !text) {
      app.pushError("请填写 person id 和覆盖文本", "savePersonOverride");
      return false;
    }
    busy.value = true;
    try {
      const scope = graph.effectiveScope();
      const data = await savePersonOverrideApi(id, text, "webui", scope);
      if (scope !== graph.effectiveScope() || id !== overrideId.value.trim()) return true;
      if (data.profile) profile.value = data.profile;
      logs.log("人工覆盖已保存", "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "savePersonOverride");
      return false;
    } finally {
      busy.value = false;
    }
  }

  async function clearOverride(): Promise<boolean> {
    const id = overrideId.value.trim();
    if (!id) {
      app.pushError("请填写 person id", "clearPersonOverride");
      return false;
    }
    busy.value = true;
    try {
      const scope = graph.effectiveScope();
      await clearPersonOverrideApi(id, scope);
      if (scope !== graph.effectiveScope() || id !== overrideId.value.trim()) return true;
      overrideText.value = "";
      if (profile.value) {
        profile.value = {
          ...profile.value,
          has_manual_override: false,
          manual_override_text: "",
          override_text: "",
        };
      }
      logs.log("人工覆盖已清除", "info");
      return true;
    } catch (err) {
      app.pushError(errText(err), "clearPersonOverride");
      return false;
    } finally {
      busy.value = false;
    }
  }

  watch(() => graph.effectiveScope(), () => {
    candidateSeq++; querySeq++; suggestSeq++;
    candidates.value = []; profile.value = null;
    overrideId.value = ""; overrideText.value = "";
    querying.value = false; loadingCandidates.value = false;
  });

  return {
    candidates,
    profile,
    keyword,
    topk,
    overrideId,
    overrideText,
    loadingCandidates,
    querying,
    busy,
    loadCandidates,
    suggestPersons,
    refreshCandidates,
    query,
    saveOverride,
    clearOverride,
  };
});
