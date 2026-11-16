<script setup lang="ts">
// 图片后台队列诊断弹窗：查看视觉向量与自动说明队列，支持推进队列、重试失败任务及 Esc 关闭。
import { onBeforeUnmount, watch } from "vue";
import { useAppStore } from "@/stores/app";
import { useImagesStore } from "@/stores/images";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const store = useImagesStore();
const app = useAppStore();

function closeModal(): void {
  if (store.jobsActionBusy) return;
  emit("close");
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key !== "Escape" || !props.open || app.confirmation) return;
  e.preventDefault();
  closeModal();
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      window.addEventListener("keydown", onKeydown);
      void store.loadJobs();
    } else {
      window.removeEventListener("keydown", onKeydown);
    }
  },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});

function formatTimestamp(ts?: number | string): string {
  if (!ts) return "-";
  const num = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(String(ts));
  if (Number.isNaN(num)) return String(ts);
  return new Date(num).toLocaleString("zh-CN", { hour12: false });
}

function statusPillClass(st?: string): string {
  if (st === "completed" || st === "ready" || st === "succeeded") return "ok";
  if (st === "failed" || st === "error") return "bad";
  if (st === "running" || st === "pending" || st === "queued") return "warn";
  return "";
}

function onFilterChange(): void {
  void store.loadJobs();
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop" @click.self="closeModal">
      <section
        class="modal-panel jobs-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="image-jobs-modal-title"
      >
        <div class="panel-title">
          <div>
            <h2 id="image-jobs-modal-title">图片任务队列诊断</h2>
            <span class="section-label">视觉向量编码与多模态说明生成队列</span>
          </div>
          <button class="btn ghost sm" type="button" :disabled="store.jobsActionBusy" @click="closeModal">
            关闭
          </button>
        </div>

        <div class="stat-strip tabular">
          <div class="stat-cell">
            <span>就绪视觉向量</span>
            <strong>{{ store.statusInfo?.stats?.ready_vector_count ?? 0 }}</strong>
          </div>
          <div class="stat-cell">
            <span>待处理（编码 / 说明）</span>
            <strong>
              {{ store.statusInfo?.stats?.pending_job_count ?? 0 }} /
              {{ store.statusInfo?.stats?.pending_description_count ?? 0 }}
            </strong>
          </div>
          <div class="stat-cell">
            <span>失败（编码 / 说明）</span>
            <strong>
              {{ store.statusInfo?.stats?.failed_job_count ?? 0 }} /
              {{ store.statusInfo?.stats?.failed_description_count ?? 0 }}
            </strong>
          </div>
          <div class="stat-cell">
            <span>维度 / 索引代次</span>
            <strong>
              {{ store.statusInfo?.dimension ?? "-" }} / G{{ store.statusInfo?.generation ?? 0 }}
            </strong>
          </div>
        </div>

        <div class="jobs-toolbar">
          <div class="field" style="max-width: 200px">
            <label for="image-jobs-filter-select">任务状态筛选</label>
            <select
              id="image-jobs-filter-select"
              v-model="store.jobStatusFilter"
              class="select"
              @change="onFilterChange"
            >
              <option value="">全部状态</option>
              <option value="pending">排队中 (pending)</option>
              <option value="running">执行中 (running)</option>
              <option value="failed">失败 (failed)</option>
              <option value="completed">已完成 (completed)</option>
            </select>
          </div>
          <div class="jobs-actions">
            <button
              class="btn"
              type="button"
              :disabled="store.jobsLoading || store.jobsActionBusy"
              @click="store.loadJobs()"
            >
              刷新队列
            </button>
            <button
              class="btn"
              type="button"
              :disabled="store.jobsActionBusy"
              @click="store.triggerRetryFailedJobs()"
            >
              重试失败任务
            </button>
            <button
              class="btn primary"
              type="button"
              :disabled="store.jobsActionBusy"
              @click="store.triggerProcessJobs()"
            >
              {{ store.jobsActionBusy ? "执行中…" : "推进队列" }}
            </button>
          </div>
        </div>

        <div v-if="store.jobsLoading" class="empty-state">正在读取队列记录…</div>
        <div v-else-if="store.jobs.length" class="jobs-list">
          <div v-for="job in store.jobs" :key="`${job.kind}-${job.id}`" class="job-row">
            <div class="job-head tabular">
              <span class="tag info">{{ job.kind === "embedding" ? "视觉向量" : "说明生成" }}</span>
              <span class="tag" :class="statusPillClass(job.status)">{{ job.status }}</span>
              <span class="mono job-asset">资产 {{ job.asset_id }}</span>
              <span class="section-label">重试 {{ job.attempt_count ?? 0 }} 次 · {{ formatTimestamp(job.updated_at) }}</span>
            </div>
            <p v-if="job.last_error" class="job-err">{{ job.last_error }}</p>
          </div>
        </div>
        <div v-else class="empty-state">
          <strong>当前筛选条件下无队列任务</strong>
          <p>后台图片编码与说明队列空闲。</p>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.jobs-modal {
  width: min(820px, 100%);
  gap: 16px;
}

.tabular {
  font-variant-numeric: tabular-nums;
}

.stat-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--hairline);
  border-bottom: 1px solid var(--hairline);
}

.stat-cell {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-cell span {
  font-size: 12px;
  color: var(--muted);
}

.stat-cell strong {
  font-size: 20px;
  line-height: 1.15;
  color: var(--ink);
  font-weight: 740;
}

@media (max-width: 680px) {
  .stat-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.jobs-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}

.jobs-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.jobs-list {
  display: flex;
  flex-direction: column;
  max-height: 360px;
  overflow-y: auto;
  padding-right: 4px;
  border-top: 1px solid var(--hairline);
}

.job-row {
  padding: 10px 0;
  border-bottom: 1px solid var(--hairline);
}

.job-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.job-asset {
  font-size: 12px;
  color: var(--ink);
}

.job-err {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--red);
  overflow-wrap: anywhere;
}
</style>
