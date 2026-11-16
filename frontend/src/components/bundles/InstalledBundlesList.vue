<script setup lang="ts">
// 右下：已安装知识包列表与安全卸载（仅清理包独占记忆，保留被共享引用的记忆）。
import { useBundlesStore } from "@/stores/bundles";

const store = useBundlesStore();

function formatTime(ts?: number | string): string {
  if (!ts) return "-";
  const num = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(String(ts));
  if (Number.isNaN(num)) return String(ts);
  return new Date(num).toLocaleString("zh-CN", { hour12: false });
}
</script>

<template>
  <div class="band installed-panel">
    <div class="panel-title">
      <div>
        <h2 class="tabular">已安装知识包（{{ store.installedItems.length }}）</h2>
        <span class="section-label">卸载时仅移除包独占条目，自动保留已被对话共享引用的记忆</span>
      </div>
      <button
        class="btn sm"
        type="button"
        :disabled="store.installedLoading"
        @click="store.loadInstalled()"
      >
        刷新列表
      </button>
    </div>

    <div v-if="store.installedLoading && !store.installedItems.length" class="empty-state">
      正在读取已安装知识包…
    </div>

    <div v-else-if="store.installedItems.length" class="rows installed-rows">
      <article
        v-for="pkg in store.installedItems"
        :key="pkg.installation_id"
        class="pkg-row"
      >
        <div class="pkg-head">
          <div class="pkg-title-block">
            <h3>{{ pkg.name || pkg.package_id }}</h3>
            <span class="section-label mono">
              {{ pkg.package_id }} · v{{ pkg.version || "1.0.0" }}
            </span>
          </div>
          <button
            class="btn danger sm"
            type="button"
            :disabled="store.uninstallingId === pkg.installation_id"
            @click="store.uninstallPackage(pkg)"
          >
            {{ store.uninstallingId === pkg.installation_id ? "卸载中…" : "卸载知识包" }}
          </button>
        </div>

        <div class="tags">
          <span class="tag info">{{ pkg.content_level || "knowledge" }}</span>
          <span class="tag tabular">段落 {{ pkg.paragraph_count ?? 0 }}</span>
          <span v-if="pkg.scope_type === 'chat' && pkg.chat_id" class="tag teal">
            会话：{{ pkg.chat_id }}
          </span>
          <span v-else class="tag">全局作用域</span>
          <span
            v-if="pkg.images && (pkg.images.assets || pkg.images.occurrences)"
            class="tag tabular"
            :class="pkg.images.retrieval_status === 'ready' ? 'ok' : 'warn'"
          >
            图片就绪 {{ pkg.images.ready ?? 0 }}/{{ pkg.images.assets ?? 0 }}
          </span>
          <span class="tag tabular">{{ formatTime(pkg.installed_at || pkg.updated_at) }}</span>
        </div>
      </article>
    </div>

    <div v-else class="empty-state">
      <strong>尚未安装外部知识包</strong>
      <p>在上方上传 `.amembundle` 知识包并通过兼容性预检后即可导入。</p>
    </div>
  </div>
</template>

<style scoped>
.installed-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.installed-rows {
  border-top: 1px solid var(--hairline);
}

.pkg-row {
  padding: 12px 2px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pkg-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.pkg-title-block {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pkg-title-block h3 {
  margin: 0;
  font-size: 14px;
  color: var(--ink);
  overflow-wrap: anywhere;
}
</style>
