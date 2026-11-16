import { defineStore } from "pinia";
import { ref, watch } from "vue";
import {
  clearPersonOverride as clearPersonOverrideApi,
  fetchPersonRegistry,
  queryPerson as queryPersonApi,
  savePersonOverride as savePersonOverrideApi,
  upsertPersonRegistry,
  type PersonProfile,
  type PersonRegistryItem,
  type PersonRegistryUpsertRequest,
} from "@/services/personApi";
import { personDisplayName } from "@/utils/personText";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";

/**
 * People store：已登记人物目录 + 画像查询/强制重算 + 登记更新 + 人工覆盖。
 *
 * 关键修复：
 * 1. 查询未命中（success === false 或 person_id 为空）时绝不将原始搜索词写入 overrideId，杜绝脏主键覆盖。
 * 2. 清除人工覆盖后立即以 force_refresh=true 重新拉取真实自动画像，消除残留人工覆盖状态。
 * 3. 请求序号守卫（H4）与记忆作用域切换重置闭环。
 */
export const usePeopleStore = defineStore("people", () => {
  const candidates = ref<PersonRegistryItem[]>([]);
  const registryTotal = ref(0);
  const registryPage = ref(1);
  const registryPageSize = ref(20);
  const registryKeyword = ref("");

  const profile = ref<PersonProfile | null>(null);
  const keyword = ref("");
  const topk = ref(12);
  const overrideId = ref("");
  const overrideText = ref("");

  const loadingCandidates = ref(false);
  const querying = ref(false);
  const busy = ref(false);
  const upserting = ref(false);

  const app = useAppStore();
  const graph = useGraphStore();
  const logs = useLogsStore();

  let candidateSeq = 0;
  let querySeq = 0;
  let suggestSeq = 0;

  /**
   * 候选下拉只读建议：不污染右侧已登记人物目录状态与分页。
   */
  async function suggestPersons(kw = ""): Promise<PersonRegistryItem[]> {
    const seq = ++suggestSeq;
    try {
      const data = await fetchPersonRegistry(kw, 1, 30, graph.effectiveScope());
      if (seq !== suggestSeq) return [];
      return data.items || [];
    } catch {
      return [];
    }
  }

  /**
   * 拉取已登记人物目录（支持关键词检索与分页）。
   */
  async function loadRegistry(page = 1, kw = registryKeyword.value): Promise<PersonRegistryItem[]> {
    candidateSeq += 1;
    const seq = candidateSeq;
    const safePage = Math.max(1, Math.floor(Number(page) || 1));
    const trimmedKw = kw.trim();
    registryKeyword.value = trimmedKw;
    loadingCandidates.value = true;
    try {
      const data = await fetchPersonRegistry(
        trimmedKw,
        safePage,
        registryPageSize.value,
        graph.effectiveScope(),
      );
      if (seq !== candidateSeq) return [];
      const items = data.items || [];
      candidates.value = items;
      registryPage.value = data.page || safePage;
      registryTotal.value = typeof data.total === "number" ? data.total : items.length;
      return items;
    } catch (err) {
      if (seq === candidateSeq) {
        app.pushError(errText(err), "人物目录");
        candidates.value = [];
        registryTotal.value = 0;
      }
      return [];
    } finally {
      if (seq === candidateSeq) loadingCandidates.value = false;
    }
  }

  /** 兼容旧调用入口。 */
  async function loadCandidates(kw = ""): Promise<PersonRegistryItem[]> {
    return loadRegistry(1, kw);
  }

  async function refreshCandidates(): Promise<void> {
    await loadRegistry(registryPage.value, registryKeyword.value);
  }

  /**
   * 查询人物画像（支持 forceRefresh 强制重算与 explicitPersonId 精确指定）。
   */
  async function query(forceRefresh = false, explicitPersonId = ""): Promise<boolean> {
    const pid = explicitPersonId.trim();
    const kw = keyword.value.trim();
    if (!pid && !kw) {
      app.pushError("请填写人物关键词或从目录选择人物", "人物画像");
      return false;
    }

    querySeq += 1;
    const seq = querySeq;
    querying.value = true;
    try {
      const data = await queryPersonApi(
        {
          ...(pid ? { person_id: pid } : {}),
          ...(kw ? { person_keyword: kw } : {}),
          top_k: Math.max(1, Math.min(50, Number(topk.value) || 12)),
          force_refresh: Boolean(forceRefresh),
        },
        graph.effectiveScope(),
      );
      if (seq !== querySeq) return false;

      const resolvedId = String(data?.person_id || "").trim();
      if (data?.success === false || !resolvedId) {
        // 严禁在未命中时用原始关键词污染 overrideId
        app.pushError(data?.error || `未找到与「${pid || kw}」匹配的人物画像`, "人物画像");
        return false;
      }

      profile.value = data;
      overrideId.value = resolvedId;
      overrideText.value = data.manual_override_text || data.override_text || "";
      if (!kw && data.person_name) {
        keyword.value = data.person_name;
      }
      logs.log(
        `${forceRefresh ? "强制重算人物画像" : "人物画像查询"}：${data.person_name || resolvedId}`,
        "info",
        "人物画像",
      );
      if (forceRefresh) {
        app.pushSuccess(`已强制重算「${data.person_name || resolvedId}」的画像`, "人物画像");
      }
      return true;
    } catch (err) {
      if (seq === querySeq) app.pushError(errText(err), "人物画像");
      return false;
    } finally {
      if (seq === querySeq) querying.value = false;
    }
  }

  /** 从已登记目录点击选中某个人物并立即加载画像。 */
  async function selectPerson(payload: { personId: string; keyword?: string }): Promise<boolean> {
    const pid = payload.personId.trim();
    if (!pid) return false;
    overrideId.value = pid;
    keyword.value = (payload.keyword || pid).trim();
    return query(false, pid);
  }

  /** 登记或更新人物目录记录（POST /v1/person/registry/upsert）。 */
  async function upsertPerson(req: PersonRegistryUpsertRequest): Promise<boolean> {
    const pid = String(req.person_id || "").trim();
    if (!pid) {
      app.pushError("请填写规范的 Person ID", "登记人物");
      return false;
    }
    upserting.value = true;
    try {
      const scope = graph.effectiveScope();
      const res = await upsertPersonRegistry({ ...req, person_id: pid }, scope);
      if (scope !== graph.effectiveScope()) return true;

      const item = res.item;
      const display = item ? personDisplayName(item) : req.person_name || pid;
      overrideId.value = pid;
      keyword.value = display || pid;
      app.pushSuccess(`人物「${display || pid}」已登记/更新`, "登记人物");
      await loadRegistry(registryPage.value, registryKeyword.value);
      await query(true, pid);
      return true;
    } catch (err) {
      app.pushError(errText(err), "登记人物");
      return false;
    } finally {
      upserting.value = false;
    }
  }

  async function saveOverride(): Promise<boolean> {
    const id = overrideId.value.trim();
    const text = overrideText.value.trim();
    if (!id || !text) {
      app.pushError("请先选定有效的 Person ID 并填写覆盖文本", "人工覆盖");
      return false;
    }
    busy.value = true;
    try {
      const scope = graph.effectiveScope();
      const data = await savePersonOverrideApi(id, text, "webui", scope);
      if (scope !== graph.effectiveScope() || id !== overrideId.value.trim()) return true;
      if (data.profile) {
        profile.value = data.profile;
      } else {
        await query(true, id);
      }
      app.pushSuccess(`已保存「${id}」的人工画像覆盖`, "人工覆盖");
      void loadRegistry(registryPage.value, registryKeyword.value);
      return true;
    } catch (err) {
      app.pushError(errText(err), "人工覆盖");
      return false;
    } finally {
      busy.value = false;
    }
  }

  async function clearOverride(): Promise<boolean> {
    const id = overrideId.value.trim();
    if (!id) {
      app.pushError("请先填写或选择有效的 Person ID", "人工覆盖");
      return false;
    }
    busy.value = true;
    try {
      const scope = graph.effectiveScope();
      await clearPersonOverrideApi(id, scope);
      if (scope !== graph.effectiveScope() || id !== overrideId.value.trim()) return true;

      overrideText.value = "";
      if (profile.value && profile.value.person_id === id) {
        profile.value = {
          ...profile.value,
          has_manual_override: false,
          manual_override_text: "",
          override_text: "",
          profile_source: "auto_snapshot",
          profile_text: profile.value.auto_profile_text || "",
        };
      }

      app.pushSuccess(`已清除「${id}」的人工覆盖，正在重算自动画像`, "人工覆盖");
      // 立即强制重算自动画像，确保 profile_text 与 profile_source 反映真实自动生成结果
      await query(true, id);
      void loadRegistry(registryPage.value, registryKeyword.value);
      return true;
    } catch (err) {
      app.pushError(errText(err), "人工覆盖");
      return false;
    } finally {
      busy.value = false;
    }
  }

  watch(
    () => graph.effectiveScope(),
    () => {
      candidateSeq += 1;
      querySeq += 1;
      suggestSeq += 1;
      candidates.value = [];
      registryTotal.value = 0;
      registryPage.value = 1;
      profile.value = null;
      overrideId.value = "";
      overrideText.value = "";
      querying.value = false;
      loadingCandidates.value = false;
      void loadRegistry(1, registryKeyword.value);
    },
  );

  return {
    candidates,
    registryTotal,
    registryPage,
    registryPageSize,
    registryKeyword,
    profile,
    keyword,
    topk,
    overrideId,
    overrideText,
    loadingCandidates,
    querying,
    busy,
    upserting,
    loadRegistry,
    loadCandidates,
    suggestPersons,
    refreshCandidates,
    query,
    selectPerson,
    upsertPerson,
    saveOverride,
    clearOverride,
  };
});
