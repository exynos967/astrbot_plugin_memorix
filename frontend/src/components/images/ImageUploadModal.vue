<script setup lang="ts">
// 上传图片入库弹窗：FileReader 读取图片并剥离 data:...;base64, 前缀，支持拖拽、键盘无障碍与 Esc 关闭。
import { onBeforeUnmount, ref, watch } from "vue";
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

const dragging = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const previewDataUrl = ref("");
const pureBase64 = ref("");
const fileName = ref("");
const fileBytes = ref(0);
const chatId = ref("");
const userStatement = ref("");
const submitting = ref(false);
const localError = ref("");

function closeModal(): void {
  if (submitting.value) return;
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
      localError.value = "";
      window.addEventListener("keydown", onKeydown);
    } else {
      window.removeEventListener("keydown", onKeydown);
    }
  },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function resetForm(): void {
  previewDataUrl.value = "";
  pureBase64.value = "";
  fileName.value = "";
  fileBytes.value = 0;
  userStatement.value = "";
  localError.value = "";
}

function loadFile(file: File): void {
  localError.value = "";
  if (!file.type.startsWith("image/")) {
    localError.value = "请选择 PNG、JPEG、WebP 或 GIF 格式的图片文件";
    return;
  }
  if (file.size > 20 * 1024 * 1024) {
    localError.value = "单张图片体积不得超过 20 MB";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const result = typeof reader.result === "string" ? reader.result : "";
    previewDataUrl.value = result;
    const commaIdx = result.indexOf(",");
    pureBase64.value = commaIdx >= 0 ? result.slice(commaIdx + 1) : result;
    fileName.value = file.name;
    fileBytes.value = file.size;
  };
  reader.onerror = () => {
    localError.value = "读取本地图片失败，请重新选择";
  };
  reader.readAsDataURL(file);
}

function onFileSelect(e: Event): void {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) loadFile(file);
}

function onDrop(e: DragEvent): void {
  dragging.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (file) loadFile(file);
}

async function handleSubmit(): Promise<void> {
  if (!pureBase64.value || submitting.value) return;
  submitting.value = true;
  const ok = await store.ingestUploadedImage({
    image_base64: pureBase64.value,
    chat_id: chatId.value,
    user_statement: userStatement.value,
  });
  submitting.value = false;
  if (ok) {
    resetForm();
    emit("close");
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop" @click.self="closeModal">
      <section
        class="modal-panel upload-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="image-upload-modal-title"
      >
        <div class="panel-title">
          <div>
            <h2 id="image-upload-modal-title">上传图片资产</h2>
            <span class="section-label">导入图片并建立哈希索引与认知说明</span>
          </div>
          <button class="btn ghost sm" type="button" :disabled="submitting" @click="closeModal">
            关闭
          </button>
        </div>

        <input
          ref="fileInput"
          type="file"
          accept="image/*"
          style="display: none"
          @change="onFileSelect"
        />

        <div
          class="dropzone"
          :class="{ dragging }"
          role="button"
          tabindex="0"
          aria-label="选择或拖拽待上传图片"
          @click="fileInput?.click()"
          @keydown.enter.prevent="fileInput?.click()"
          @keydown.space.prevent="fileInput?.click()"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="onDrop"
        >
          <template v-if="previewDataUrl">
            <img :src="previewDataUrl" :alt="fileName || '已选图片'" class="upload-preview-img" />
            <div class="upload-file-meta tabular">
              <strong>{{ fileName }}</strong>
              <span>{{ formatSize(fileBytes) }} · 点击更换文件</span>
            </div>
          </template>
          <template v-else>
            <strong>点击选择图片，或将文件拖拽至此处</strong>
            <span>支持 PNG、JPG、WebP、GIF（最大 20 MB）</span>
          </template>
        </div>

        <p v-if="localError" class="local-err" role="alert">{{ localError }}</p>

        <div class="grid-2 upload-fields">
          <div class="field">
            <label for="upload-image-chat-id">归属会话 ID（留空归入全局）</label>
            <input
              id="upload-image-chat-id"
              v-model="chatId"
              class="input"
              list="memorix-image-chats-datalist"
              placeholder="例如 group_123456"
            />
            <datalist id="memorix-image-chats-datalist">
              <option v-for="c in store.chatStats" :key="c.chat_id" :value="c.chat_id" />
            </datalist>
          </div>
          <div class="field">
            <label for="upload-image-statement">初始认知说明（可选）</label>
            <input
              id="upload-image-statement"
              v-model="userStatement"
              class="input"
              placeholder="描述图片主体、人物或用途"
              @keyup.enter="handleSubmit"
            />
          </div>
        </div>

        <div class="modal-footer">
          <button class="btn" type="button" :disabled="submitting" @click="closeModal">取消</button>
          <button
            class="btn primary"
            type="button"
            :disabled="!pureBase64 || submitting"
            @click="handleSubmit"
          >
            {{ submitting ? "入库中…" : "上传入库" }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.upload-modal {
  width: min(620px, 100%);
  gap: 16px;
}

.tabular {
  font-variant-numeric: tabular-nums;
}

.dropzone:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.upload-preview-img {
  max-width: 100%;
  max-height: 220px;
  border-radius: var(--radius-md);
  object-fit: contain;
  border: 1px solid var(--border-color);
}

.upload-file-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12.5px;
}

.upload-file-meta strong {
  color: var(--ink);
}

.upload-fields {
  margin-top: 2px;
}

.local-err {
  margin: 0;
  color: var(--red);
  font-size: 12.5px;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 4px;
}
</style>
