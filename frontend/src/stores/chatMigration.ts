import { defineStore } from "pinia";
import { computed, reactive, ref, watch } from "vue";
import {
  chatMigrationApi,
  type ChatMigrationJob,
  type MigrationChatCandidate,
} from "@/services/chatMigrationApi";
import { useAppStore } from "@/stores/app";
import { useGraphStore } from "@/stores/graph";
import { errText } from "@/utils/error";

export const useChatMigrationStore = defineStore("chatMigration", () => {
  const graph = useGraphStore();
  const app = useAppStore();

  const scope = computed(() => graph.effectiveScope());
  let chatsSeq = 0;
  let jobsSeq = 0;

  const chatsLoading = ref(false);
  const jobsLoading = ref(false);
  const previewing = ref(false);
  const executing = ref(false);
  const recoveringId = ref("");

  const chatSearch = ref("");
  const chats = ref<MigrationChatCandidate[]>([]);
  const jobs = ref<ChatMigrationJob[]>([]);
  const activeJob = ref<ChatMigrationJob | null>(null);
  const confirmOverwrite = ref(false);

  const form = reactive({
    sourceChatId: "",
    targetChatId: "",
    migrateTranscript: true,
    note: "",
  });

  async function loadChats(): Promise<void> {
    const seq = ++chatsSeq;
    const reqScope = scope.value;
    chatsLoading.value = true;
    try {
      const res = await chatMigrationApi.listChats(chatSearch.value, 120, reqScope);
      if (seq !== chatsSeq || reqScope !== scope.value) return;
      chats.value = Array.isArray(res?.items) ? res.items : [];
    } catch (err) {
      if (seq === chatsSeq && reqScope === scope.value) {
        app.pushError(errText(err), "加载会话列表");
      }
    } finally {
      if (seq === chatsSeq) chatsLoading.value = false;
    }
  }

  async function loadJobs(): Promise<void> {
    const seq = ++jobsSeq;
    const reqScope = scope.value;
    jobsLoading.value = true;
    try {
      const res = await chatMigrationApi.listJobs(30, reqScope);
      if (seq !== jobsSeq || reqScope !== scope.value) return;
      jobs.value = Array.isArray(res?.items) ? res.items : [];
    } catch (err) {
      if (seq === jobsSeq && reqScope === scope.value) {
        app.pushError(errText(err), "加载迁移历史");
      }
    } finally {
      if (seq === jobsSeq) jobsLoading.value = false;
    }
  }

  async function refreshAll(): Promise<void> {
    await Promise.all([loadChats(), loadJobs()]);
  }

  function swapEndpoints(): void {
    const prevSource = form.sourceChatId;
    form.sourceChatId = form.targetChatId;
    form.targetChatId = prevSource;
    activeJob.value = null;
    confirmOverwrite.value = false;
  }

  async function runPreview(): Promise<void> {
    const src = form.sourceChatId.trim();
    const dst = form.targetChatId.trim();
    if (!src || !dst) {
      app.pushError("请填写源会话 ID 与目标会话 ID", "迁移预检");
      return;
    }
    if (src === dst) {
      app.pushError("源会话与目标会话不能相同", "迁移预检");
      return;
    }
    const reqScope = scope.value;
    previewing.value = true;
    confirmOverwrite.value = false;
    try {
      const res = await chatMigrationApi.preview(
        {
          source_chat_id: src,
          target_chat_id: dst,
          migrate_transcript: form.migrateTranscript,
          note: form.note.trim(),
        },
        reqScope,
      );
      if (reqScope !== scope.value) return;
      if (!res?.success || !res.job) {
        app.pushError(res?.error || "生成迁移预检快照失败", "迁移预检");
        return;
      }
      activeJob.value = res.job;
      await loadJobs();
      app.pushSuccess(`已生成预检快照 ${res.job.job_id}`, "迁移预检");
    } catch (err) {
      app.pushError(errText(err), "迁移预检");
    } finally {
      previewing.value = false;
    }
  }

  async function executeActiveJob(refreshFingerprint = false): Promise<void> {
    const job = activeJob.value;
    if (!job) return;
    if (
      form.sourceChatId.trim() !== job.source_chat_id ||
      form.targetChatId.trim() !== job.target_chat_id ||
      form.migrateTranscript !== job.migrate_transcript
    ) {
      app.pushError("迁移端点或选项已变更，请重新生成预检快照后再执行", "执行会话迁移");
      return;
    }
    if (job.target_has_existing_data && !confirmOverwrite.value) {
      app.pushError("目标会话已存在数据，请先勾选归档确认项", "执行会话迁移");
      return;
    }
    const reqScope = scope.value;
    executing.value = true;
    try {
      const res = await chatMigrationApi.execute(
        job.job_id,
        {
          confirmed: Boolean(confirmOverwrite.value || !job.target_has_existing_data),
          refresh_fingerprint: refreshFingerprint,
        },
        reqScope,
      );
      if (reqScope !== scope.value) return;
      if (res?.job) {
        activeJob.value = res.job;
      }
      if (!res?.success) {
        app.pushError(res?.error || "执行会话迁移失败", "执行会话迁移");
        await loadJobs();
        return;
      }
      confirmOverwrite.value = false;
      app.pushSuccess(
        `已完成会话记忆迁移：${job.source_chat_id} → ${job.target_chat_id}`,
        "执行会话迁移",
      );
      await refreshAll();
    } catch (err) {
      app.pushError(errText(err), "执行会话迁移");
    } finally {
      executing.value = false;
    }
  }

  async function recoverJob(job: ChatMigrationJob): Promise<void> {
    if (!job?.job_id) return;
    const confirmed = await app.requestConfirmation({
      title: "回滚会话记忆迁移",
      message: `确定按原子账本无损回滚迁移任务「${job.job_id}」（${job.source_chat_id} → ${job.target_chat_id}）吗？归档的目标会话旧记录将同步恢复。`,
      confirmText: "回滚迁移",
      danger: true,
    });
    if (!confirmed) return;

    const reqScope = scope.value;
    recoveringId.value = job.job_id;
    try {
      const res = await chatMigrationApi.recover(job.job_id, reqScope);
      if (reqScope !== scope.value) return;
      if (res?.job && activeJob.value?.job_id === res.job.job_id) {
        activeJob.value = res.job;
      }
      if (!res?.success) {
        app.pushError(res?.error || "恢复迁移任务失败", "恢复会话迁移");
        return;
      }
      app.pushSuccess(`已无损回滚迁移任务 ${job.job_id}`, "恢复会话迁移");
      await refreshAll();
    } catch (err) {
      app.pushError(errText(err), "恢复会话迁移");
    } finally {
      recoveringId.value = "";
    }
  }

  function selectJob(job: ChatMigrationJob): void {
    activeJob.value = job;
    form.sourceChatId = job.source_chat_id;
    form.targetChatId = job.target_chat_id;
    form.migrateTranscript = job.migrate_transcript;
    confirmOverwrite.value = false;
  }

  watch(scope, (next, prev) => {
    if (next === prev) return;
    chatsSeq += 1;
    jobsSeq += 1;
    activeJob.value = null;
    confirmOverwrite.value = false;
    void refreshAll();
  });

  return {
    scope,
    chatsLoading,
    jobsLoading,
    previewing,
    executing,
    recoveringId,
    chatSearch,
    chats,
    jobs,
    activeJob,
    confirmOverwrite,
    form,
    loadChats,
    loadJobs,
    refreshAll,
    swapEndpoints,
    runPreview,
    executeActiveJob,
    recoverJob,
    selectJob,
  };
});
