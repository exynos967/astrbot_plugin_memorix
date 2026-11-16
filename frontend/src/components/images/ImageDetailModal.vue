<script setup lang="ts">
// 图片记忆详情弹窗：智能横竖图自适应分栏，扁平化 hairline 行布局（<=2 层嵌套），支持 Esc 关闭与完整认知治理。
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { copyTextSafe } from "@/services/api";
import { resolveObservationId, type ImageObservation } from "@/services/imageApi";
import { useAppStore } from "@/stores/app";
import { useImagesStore } from "@/stores/images";

const store = useImagesStore();
const app = useAppStore();

const detailTab = ref<"observations" | "links" | "occurrences">("observations");

// 行内修正说明状态
const editingObsId = ref("");
const editingObsText = ref("");
const editingBusy = ref(false);

// 新增手动说明状态
const selectedOccurrenceId = ref("");
const newObsText = ref("");
const addingObs = ref(false);

// 新增关联记忆状态
const linkTargetType = ref("paragraph");
const linkTargetId = ref("");
const linkKind = ref("evidence");
const addingLink = ref(false);

const asset = computed(() => store.activeDetail?.asset ?? null);
const occurrences = computed(() => store.activeDetail?.occurrences ?? []);
const observations = computed(() => store.activeDetail?.observations ?? []);
const links = computed(() => store.activeDetail?.links ?? []);

const isBusy = computed(() => editingBusy.value || addingObs.value || addingLink.value);

// 宽幅横图采用上下布局避免图片被挤压过小；竖图/方图采用左右分栏
const isWideLandscape = computed(() => {
  const w = asset.value?.width ?? 0;
  const h = asset.value?.height ?? 0;
  return w > 0 && h > 0 && w / h >= 1.65;
});

function closeModal(): void {
  if (isBusy.value) return;
  cancelEditObservation();
  store.closeDetail();
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key !== "Escape" || !store.activeAssetId || app.confirmation) return;
  e.preventDefault();
  closeModal();
}

watch(
  () => Boolean(store.activeAssetId),
  (open) => {
    if (open) {
      detailTab.value = "observations";
      cancelEditObservation();
      window.addEventListener("keydown", onKeydown);
    } else {
      window.removeEventListener("keydown", onKeydown);
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});

watch(
  occurrences,
  (list) => {
    if (list.length && !list.some((o) => o.occurrence_id === selectedOccurrenceId.value)) {
      selectedOccurrenceId.value = list[0].occurrence_id;
    }
  },
  { immediate: true },
);

function formatBytes(bytes?: number): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatTime(ts?: number | string): string {
  if (!ts) return "-";
  const num = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(String(ts));
  if (Number.isNaN(num)) return String(ts);
  return new Date(num).toLocaleString("zh-CN", { hour12: false });
}

function startEditObservation(obs: ImageObservation): void {
  editingObsId.value = resolveObservationId(obs);
  editingObsText.value = obs.text;
}

function cancelEditObservation(): void {
  editingObsId.value = "";
  editingObsText.value = "";
}

async function submitEditObservation(obs: ImageObservation): Promise<void> {
  editingBusy.value = true;
  const ok = await store.saveObservationText({
    occurrenceId: obs.occurrence_id,
    text: editingObsText.value,
    supersedesId: resolveObservationId(obs) || undefined,
  });
  editingBusy.value = false;
  if (ok) cancelEditObservation();
}

async function submitNewObservation(): Promise<void> {
  const occId = selectedOccurrenceId.value || occurrences.value[0]?.occurrence_id || "";
  if (!occId || !newObsText.value.trim()) return;
  addingObs.value = true;
  const ok = await store.saveObservationText({
    occurrenceId: occId,
    text: newObsText.value,
  });
  addingObs.value = false;
  if (ok) newObsText.value = "";
}

async function submitNewLink(): Promise<void> {
  const occId = selectedOccurrenceId.value || occurrences.value[0]?.occurrence_id || "";
  if (!occId || !linkTargetId.value.trim()) return;
  addingLink.value = true;
  const ok = await store.addMemoryLink({
    occurrenceId: occId,
    targetType: linkTargetType.value,
    targetId: linkTargetId.value,
    linkKind: linkKind.value,
  });
  addingLink.value = false;
  if (ok) linkTargetId.value = "";
}

async function copyHash(hash?: string): Promise<void> {
  if (!hash) return;
  const ok = await copyTextSafe(hash);
  if (ok) app.pushSuccess("内容哈希已复制", "图片详情");
}

function triggerFindSimilar(): void {
  if (!asset.value?.content_hash) return;
  const hash = asset.value.content_hash;
  const short = hash.slice(0, 10);
  store.closeDetail();
  void store.runVisualSearch({
    contentHash: hash,
    label: `哈希 ${short}`,
  });
}
</script>

<template>
  <Teleport to="body">
    <div v-if="store.activeAssetId" class="modal-backdrop" @click.self="closeModal">
      <section
        class="modal-panel detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="image-detail-modal-title"
      >
        <div class="panel-title">
          <div>
            <h2 id="image-detail-modal-title">图片资产详情</h2>
            <span v-if="asset" class="section-label mono">ID {{ asset.asset_id }}</span>
          </div>
          <div class="detail-top-actions">
            <button
              v-if="asset?.content_hash"
              class="btn"
              type="button"
              @click="triggerFindSimilar"
            >
              检索相似图片
            </button>
            <button class="btn ghost sm" type="button" :disabled="isBusy" @click="closeModal">
              关闭
            </button>
          </div>
        </div>

        <div v-if="store.detailLoading && !asset" class="empty-state">正在读取图片详情…</div>

        <div v-else-if="asset" class="detail-layout" :class="{ landscape: isWideLandscape }">
          <!-- 左侧/上方：图片预览与结构化技术标签（无冗余解释性图注） -->
          <div class="detail-media-pane">
            <div class="detail-img-frame">
              <img
                v-if="store.getPreviewUrl(asset.asset_id)"
                :src="store.getPreviewUrl(asset.asset_id)"
                :alt="`图片资产 ${asset.asset_id.slice(0, 10)}`"
              />
              <div v-else class="empty-state">正在加载原图…</div>
            </div>
            <div class="tags tabular">
              <span class="tag ok">{{ asset.status === "ready" || !asset.status ? "就绪" : asset.status }}</span>
              <span v-if="asset.width && asset.height" class="tag mono">
                {{ asset.width }} × {{ asset.height }}
              </span>
              <span class="tag">{{ formatBytes(asset.byte_size) }}</span>
              <span v-if="asset.mime_type" class="tag">{{ asset.mime_type }}</span>
              <button
                v-if="asset.content_hash"
                type="button"
                class="tag mono hash-btn"
                title="点击复制完整 SHA256 内容哈希"
                @click="copyHash(asset.content_hash)"
              >
                SHA {{ asset.content_hash.slice(0, 12) }}
              </button>
            </div>
          </div>

          <!-- 右侧/下方治理工作区：扁平化分割行 -->
          <div class="detail-work-pane">
            <div class="subtabs">
              <button
                type="button"
                class="subtab-btn tabular"
                :class="{ active: detailTab === 'observations' }"
                @click="detailTab = 'observations'"
              >
                认知说明 ({{ observations.length }})
              </button>
              <button
                type="button"
                class="subtab-btn tabular"
                :class="{ active: detailTab === 'links' }"
                @click="detailTab = 'links'"
              >
                关联记忆 ({{ links.length }})
              </button>
              <button
                type="button"
                class="subtab-btn tabular"
                :class="{ active: detailTab === 'occurrences' }"
                @click="detailTab = 'occurrences'"
              >
                出现记录 ({{ occurrences.length }})
              </button>
            </div>

            <!-- 1. 认知说明 -->
            <div v-if="detailTab === 'observations'" class="tab-body">
              <div v-if="observations.length" class="flat-rows">
                <div
                  v-for="obs in observations"
                  :key="resolveObservationId(obs) || obs.text"
                  class="flat-row"
                  :class="{ superseded: Boolean(obs.superseded_by) }"
                >
                  <div class="flat-row-head">
                    <div class="tags" style="margin-top: 0">
                      <span
                        class="tag"
                        :class="obs.confirm_status === 'confirmed' ? 'ok' : 'warn'"
                      >
                        {{ obs.confirm_status === "confirmed" ? "已确认" : obs.confirm_status || "待确认" }}
                      </span>
                      <span class="tag">{{ obs.source_kind || "auto" }}</span>
                      <span v-if="obs.superseded_by" class="tag">已被更替</span>
                    </div>
                    <div v-if="!obs.superseded_by" class="obs-btn-group">
                      <button
                        v-if="obs.confirm_status !== 'confirmed'"
                        class="btn sm"
                        type="button"
                        @click="store.confirmObservation(obs)"
                      >
                        确认说明
                      </button>
                      <button
                        class="btn sm"
                        type="button"
                        @click="startEditObservation(obs)"
                      >
                        修正说明
                      </button>
                    </div>
                  </div>

                  <div
                    v-if="editingObsId && editingObsId === resolveObservationId(obs)"
                    class="inline-editor"
                  >
                    <textarea v-model="editingObsText" class="textarea" rows="3" aria-label="修正认知说明文本" />
                    <div class="result-actions">
                      <button
                        class="btn primary sm"
                        type="button"
                        :disabled="editingBusy || !editingObsText.trim()"
                        @click="submitEditObservation(obs)"
                      >
                        保存修正
                      </button>
                      <button class="btn sm" type="button" @click="cancelEditObservation">
                        取消
                      </button>
                    </div>
                  </div>
                  <p v-else class="flat-row-text">{{ obs.text }}</p>
                </div>
              </div>
              <div v-else class="empty-state">
                <strong>暂无认知说明</strong>
                <p>可在下方补充关于该图片对象、场景或语义的确认说明。</p>
              </div>

              <div v-if="occurrences.length" class="inline-form-section">
                <h3>添加认知说明</h3>
                <div class="field">
                  <label for="img-obs-occ-select">绑定出现记录</label>
                  <select id="img-obs-occ-select" v-model="selectedOccurrenceId" class="select">
                    <option
                      v-for="occ in occurrences"
                      :key="occ.occurrence_id"
                      :value="occ.occurrence_id"
                    >
                      {{ occ.chat_id || "全局" }} · {{ formatTime(occ.occurred_at || occ.created_at) }} ({{ occ.occurrence_id.slice(0, 8) }})
                    </option>
                  </select>
                </div>
                <div class="field">
                  <label for="img-obs-new-text">说明内容</label>
                  <textarea
                    id="img-obs-new-text"
                    v-model="newObsText"
                    class="textarea"
                    rows="2"
                    placeholder="输入图片认知说明，保存后自动标记为已确认"
                  />
                </div>
                <div>
                  <button
                    class="btn primary sm"
                    type="button"
                    :disabled="addingObs || !newObsText.trim()"
                    @click="submitNewObservation"
                  >
                    {{ addingObs ? "保存中…" : "添加说明" }}
                  </button>
                </div>
              </div>
            </div>

            <!-- 2. 关联记忆 -->
            <div v-else-if="detailTab === 'links'" class="tab-body">
              <div v-if="links.length" class="flat-rows">
                <div v-for="lk in links" :key="lk.link_id" class="flat-row">
                  <div class="flat-row-head">
                    <div class="tags" style="margin-top: 0">
                      <span class="tag info">{{ lk.target_type }}</span>
                      <span class="tag teal">{{ lk.link_kind || "evidence" }}</span>
                      <span class="mono section-label">{{ lk.target_id }}</span>
                    </div>
                    <button
                      class="btn danger sm"
                      type="button"
                      @click="store.removeMemoryLink(lk.link_id)"
                    >
                      解除关联
                    </button>
                  </div>
                  <p v-if="lk.memory?.content || lk.memory?.summary" class="flat-row-text">
                    {{ lk.memory.content || lk.memory.summary }}
                  </p>
                </div>
              </div>
              <div v-else class="empty-state">
                <strong>暂无关联记忆</strong>
                <p>可将此图片绑定至对应的段落、实体、关系或情景摘要。</p>
              </div>

              <div v-if="occurrences.length" class="inline-form-section">
                <h3>建立记忆关联</h3>
                <div class="grid-2">
                  <div class="field">
                    <label for="img-link-target-type">目标类型</label>
                    <select id="img-link-target-type" v-model="linkTargetType" class="select">
                      <option value="paragraph">记忆段落 (paragraph)</option>
                      <option value="entity">图谱实体 (entity)</option>
                      <option value="relation">图谱关系 (relation)</option>
                      <option value="episode">情景摘要 (episode)</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="img-link-role">关联角色</label>
                    <select id="img-link-role" v-model="linkKind" class="select">
                      <option value="evidence">事实证据 (evidence)</option>
                      <option value="subject">主体对象 (subject)</option>
                      <option value="context">上下文背景 (context)</option>
                    </select>
                  </div>
                </div>
                <div class="field">
                  <label for="img-link-target-id">目标标识或名称</label>
                  <input
                    id="img-link-target-id"
                    v-model="linkTargetId"
                    class="input"
                    placeholder="输入段落哈希、实体名称或情景 ID"
                    @keyup.enter="submitNewLink"
                  />
                </div>
                <div>
                  <button
                    class="btn primary sm"
                    type="button"
                    :disabled="addingLink || !linkTargetId.trim()"
                    @click="submitNewLink"
                  >
                    {{ addingLink ? "关联中…" : "建立关联" }}
                  </button>
                </div>
              </div>
            </div>

            <!-- 3. 出现记录 -->
            <div v-else class="tab-body">
              <div v-if="occurrences.length" class="flat-rows">
                <div v-for="occ in occurrences" :key="occ.occurrence_id" class="flat-row">
                  <div class="flat-row-head">
                    <div>
                      <strong class="occ-title">{{ occ.chat_id ? `会话 ${occ.chat_id}` : "全局作用域" }}</strong>
                      <span class="cell-note tabular">
                        {{ formatTime(occ.occurred_at || occ.created_at) }} · 来源 {{ occ.source_kind || "chat" }}
                      </span>
                    </div>
                    <button
                      class="btn danger sm"
                      type="button"
                      @click="store.removeOccurrence(occ.occurrence_id)"
                    >
                      删除记录
                    </button>
                  </div>
                  <div class="tags">
                    <span class="tag mono">ID {{ occ.occurrence_id.slice(0, 12) }}</span>
                    <span v-if="occ.message_id" class="tag mono">消息 {{ occ.message_id }}</span>
                    <span v-if="occ.installation_id" class="tag info">来自知识包</span>
                  </div>
                </div>
              </div>
              <div v-else class="empty-state">暂无出现记录</div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.detail-modal {
  width: min(980px, 100%);
  gap: 16px;
}

.tabular {
  font-variant-numeric: tabular-nums;
}

.detail-top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.detail-layout {
  display: grid;
  grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
  gap: 20px;
  min-height: 0;
}

.detail-layout.landscape {
  grid-template-columns: 1fr;
}

.detail-media-pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-img-frame {
  display: grid;
  place-items: center;
  min-height: 220px;
  max-height: 440px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--surface-soft);
  overflow: hidden;
}

.detail-img-frame img {
  max-width: 100%;
  max-height: 414px;
  border-radius: var(--radius-sm);
  object-fit: contain;
}

.hash-btn {
  cursor: pointer;
}

.hash-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.detail-work-pane {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tab-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 460px;
  overflow-y: auto;
  padding-right: 4px;
}

.flat-rows {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--hairline);
}

.flat-row {
  padding: 12px 0;
  border-bottom: 1px solid var(--hairline);
}

.flat-row.superseded {
  opacity: 0.6;
}

.flat-row-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.flat-row-text {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.58;
  color: var(--text);
  overflow-wrap: anywhere;
}

.occ-title {
  display: block;
  font-size: 13.5px;
  color: var(--ink);
}

.obs-btn-group {
  display: flex;
  gap: 6px;
}

.inline-editor {
  margin-top: 8px;
}

.inline-form-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--hairline);
}

.inline-form-section h3 {
  margin: 0;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--ink);
}

@media (max-width: 820px) {
  .detail-layout {
    grid-template-columns: 1fr;
  }
}
</style>
