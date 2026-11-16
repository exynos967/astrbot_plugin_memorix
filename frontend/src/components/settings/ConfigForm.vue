<script setup lang="ts">
// v2.0 运行配置表单：
// 1. 支持路由查询深链（?group=image_memory / ?group=fuzzy_modify / ?group=integration 等）自动激活分组并平滑定位。
// 2. 扁平化层级（消除 .band > .settings-group > .config-field-card 三层盒套盒），采用清晰顶部分割线与双栏字段行。
// 3. 修复 number 与 list 输入框聚焦态草稿同步缺陷，输入时即时更新脏字段状态且不打断光标。
import { computed, nextTick, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
  CONFIG_FIELDS,
  formatListValue,
  isMaskedSecret,
  parseListInput,
  resolveConfigGroupId,
  type ConfigField,
  type ConfigFieldGroup,
} from "@/utils/configFields";
import { useSettingsStore } from "@/stores/settings";

const route = useRoute();
const settings = useSettingsStore();

const shellRef = ref<HTMLElement | null>(null);
const activeGroupId = ref<string>("all");
const onlyDirty = ref(false);
const searchKeyword = ref("");

// number 字段聚焦编辑草稿缓冲（避免输入 "0."、"-" 或删除清空时被强转 0）
const numberDrafts = reactive<Record<string, string>>({});
const focusedNumberKey = ref<string>("");

// list 字段聚焦编辑草稿缓冲（输入时实时同步数组脏状态，且不重排用户正在输入的逗号/换行）
const listDrafts = reactive<Record<string, string>>({});
const focusedListKey = ref<string>("");

// password 字段明文显示开关
const revealedPasswords = reactive<Record<string, boolean>>({});

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function groupDirtyCount(group: ConfigFieldGroup): number {
  let count = 0;
  for (const f of group.fields) {
    if (settings.isFieldDirty(f.key)) count += 1;
  }
  return count;
}

function selectGroup(groupId: string): void {
  activeGroupId.value = groupId;
  if (groupId === "all") {
    onlyDirty.value = false;
  }
}

async function applyRouteGroupDeepLink(rawGroup: unknown): Promise<void> {
  const resolved = resolveConfigGroupId(rawGroup);
  if (!resolved) return;
  onlyDirty.value = false;
  searchKeyword.value = "";
  activeGroupId.value = resolved;
  await nextTick();
  const targetEl =
    shellRef.value?.querySelector<HTMLElement>(`[data-config-group="${resolved}"]`) ??
    shellRef.value;
  targetEl?.scrollIntoView({
    behavior: prefersReducedMotion() ? "auto" : "smooth",
    block: "start",
  });
}

watch(
  () => route.query.group,
  (groupQuery) => {
    if (groupQuery !== undefined) {
      void applyRouteGroupDeepLink(groupQuery);
    }
  },
  { immediate: true },
);

watch(
  () => [settings.rawConfig, settings.dirtyCount] as const,
  ([, count]) => {
    if (count === 0) {
      if (!focusedNumberKey.value) {
        for (const k of Object.keys(numberDrafts)) delete numberDrafts[k];
      }
      if (!focusedListKey.value) {
        for (const k of Object.keys(listDrafts)) delete listDrafts[k];
      }
    }
  },
);

const filteredGroups = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase();
  const result: Array<{
    id: string;
    title: string;
    description?: string;
    fields: ConfigField[];
  }> = [];

  for (const group of CONFIG_FIELDS) {
    if (activeGroupId.value !== "all" && group.id !== activeGroupId.value) {
      continue;
    }
    const matchedFields = group.fields.filter((f) => {
      if (onlyDirty.value && !settings.isFieldDirty(f.key)) return false;
      if (!kw) return true;
      return (
        f.label.toLowerCase().includes(kw) ||
        f.key.toLowerCase().includes(kw) ||
        Boolean(f.hint && f.hint.toLowerCase().includes(kw))
      );
    });
    if (matchedFields.length > 0) {
      result.push({
        id: group.id,
        title: group.title,
        description: group.description,
        fields: matchedFields,
      });
    }
  }
  return result;
});

function displayNumberValue(field: ConfigField): string {
  if (focusedNumberKey.value === field.key && field.key in numberDrafts) {
    return numberDrafts[field.key];
  }
  const val = settings.form[field.key];
  if (typeof val === "number" && Number.isFinite(val)) return String(val);
  const fallback = typeof field.default === "number" ? field.default : (field.min ?? 0);
  return String(fallback);
}

function onNumberFocus(field: ConfigField): void {
  focusedNumberKey.value = field.key;
  numberDrafts[field.key] = displayNumberValue(field);
}

function onNumberInput(field: ConfigField, event: Event): void {
  const raw = (event.target as HTMLInputElement).value;
  numberDrafts[field.key] = raw;
  const trimmed = raw.trim();
  if (
    trimmed === "" ||
    trimmed === "-" ||
    trimmed === "." ||
    trimmed === "-." ||
    /\.\d*0$/.test(trimmed) ||
    trimmed.endsWith(".")
  ) {
    return;
  }
  const parsed = Number(trimmed);
  if (Number.isFinite(parsed)) {
    settings.form[field.key] = parsed;
  }
}

function commitNumberField(field: ConfigField): void {
  const raw = (numberDrafts[field.key] ?? "").trim();
  delete numberDrafts[field.key];
  if (focusedNumberKey.value === field.key) {
    focusedNumberKey.value = "";
  }
  if (!raw || raw === "-" || raw === "." || raw === "-.") {
    const base = settings.baseline[field.key];
    const fallback =
      typeof base === "number"
        ? base
        : typeof field.default === "number"
          ? field.default
          : (field.min ?? 0);
    settings.form[field.key] = fallback;
    return;
  }
  let num = Number(raw);
  if (!Number.isFinite(num)) {
    const fallback = typeof field.default === "number" ? field.default : (field.min ?? 0);
    settings.form[field.key] = fallback;
    return;
  }
  if (field.min !== undefined && num < field.min) num = field.min;
  if (field.max !== undefined && num > field.max) num = field.max;
  settings.form[field.key] = num;
}

function displayListValue(field: ConfigField): string {
  if (focusedListKey.value === field.key && field.key in listDrafts) {
    return listDrafts[field.key];
  }
  return formatListValue(settings.form[field.key]);
}

function onListFocus(field: ConfigField): void {
  focusedListKey.value = field.key;
  listDrafts[field.key] = displayListValue(field);
}

function onListInput(field: ConfigField, event: Event): void {
  const raw = (event.target as HTMLTextAreaElement).value;
  listDrafts[field.key] = raw;
  settings.form[field.key] = parseListInput(raw);
}

function commitListField(field: ConfigField): void {
  const raw = listDrafts[field.key];
  delete listDrafts[field.key];
  if (focusedListKey.value === field.key) {
    focusedListKey.value = "";
  }
  if (raw !== undefined) {
    settings.form[field.key] = parseListInput(raw);
  }
}

function onBooleanChange(field: ConfigField, event: Event): void {
  settings.form[field.key] = (event.target as HTMLSelectElement).value === "true";
}

function onStringInput(field: ConfigField, event: Event): void {
  settings.form[field.key] = (event.target as HTMLInputElement).value;
}

function onSelectChange(field: ConfigField, event: Event): void {
  settings.form[field.key] = (event.target as HTMLSelectElement).value;
}

function togglePasswordReveal(key: string): void {
  revealedPasswords[key] = !revealedPasswords[key];
}

function isSecretMaskedNow(key: string): boolean {
  return isMaskedSecret(settings.form[key]);
}
</script>

<template>
  <div ref="shellRef" class="config-form-shell">
    <!-- 分组标签与检索过滤栏（扁平无嵌套卡片） -->
    <div class="config-filter-bar">
      <div class="subtabs group-tabs" role="tablist" aria-label="运行配置分组">
        <button
          type="button"
          role="tab"
          class="subtab-btn"
          :class="{ active: activeGroupId === 'all' && !onlyDirty }"
          :aria-selected="activeGroupId === 'all' && !onlyDirty"
          @click="selectGroup('all')"
        >
          全部分组
        </button>
        <button
          v-for="g in CONFIG_FIELDS"
          :key="g.id"
          type="button"
          role="tab"
          class="subtab-btn"
          :class="{ active: activeGroupId === g.id }"
          :aria-selected="activeGroupId === g.id"
          @click="selectGroup(g.id)"
        >
          <span>{{ g.title }}</span>
          <span v-if="groupDirtyCount(g) > 0" class="tab-dirty-pill tabular-nums">
            {{ groupDirtyCount(g) }}
          </span>
        </button>
      </div>

      <div class="config-search-row">
        <div class="config-search-box">
          <input
            v-model="searchKeyword"
            class="input"
            type="search"
            placeholder="搜索配置名称、键名（如 image_memory）或说明…"
            aria-label="搜索配置项"
          />
          <button
            v-if="searchKeyword.trim()"
            type="button"
            class="btn sm"
            @click="searchKeyword = ''"
          >
            清空搜索
          </button>
        </div>

        <label class="dirty-toggle">
          <input v-model="onlyDirty" type="checkbox" />
          <span class="tabular-nums">仅看已修改（{{ settings.dirtyCount }}）</span>
        </label>
      </div>
    </div>

    <div v-if="!filteredGroups.length" class="empty">
      未找到匹配的配置项，请切换分组标签或清空筛选条件
    </div>

    <div v-else class="config-group-stack">
      <section
        v-for="group in filteredGroups"
        :key="group.id"
        :data-config-group="group.id"
        class="config-group-section"
      >
        <div class="group-header">
          <div>
            <h3>{{ group.title }}</h3>
            <p v-if="group.description" class="group-desc">{{ group.description }}</p>
          </div>
          <span class="tag tabular-nums">{{ group.fields.length }} 项</span>
        </div>

        <div class="config-field-grid">
          <div
            v-for="field in group.fields"
            :key="field.key"
            class="field config-field-row"
            :class="{
              'is-dirty': settings.isFieldDirty(field.key),
              'span-wide': field.type === 'list' || field.type === 'password',
            }"
          >
            <div class="field-label-row">
              <label :for="`cfg-${field.key}`" :title="field.key">
                {{ field.label }}
              </label>
              <div class="tags field-badges">
                <span v-if="settings.isFieldDirty(field.key)" class="tag warn">已修改</span>
                <span v-if="field.runtimeOnly" class="tag">仅运行时</span>
              </div>
            </div>
            <div class="field-key mono">{{ field.key }}</div>

            <!-- 1. boolean -->
            <select
              v-if="field.type === 'boolean'"
              :id="`cfg-${field.key}`"
              class="select"
              :value="settings.form[field.key] === true ? 'true' : 'false'"
              @change="onBooleanChange(field, $event)"
            >
              <option value="true">开启</option>
              <option value="false">关闭</option>
            </select>

            <!-- 2. select -->
            <select
              v-else-if="field.type === 'select'"
              :id="`cfg-${field.key}`"
              class="select"
              :value="String(settings.form[field.key] ?? field.default)"
              @change="onSelectChange(field, $event)"
            >
              <option
                v-for="opt in field.options || []"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>

            <!-- 3. number -->
            <input
              v-else-if="field.type === 'number'"
              :id="`cfg-${field.key}`"
              class="input tabular-nums"
              type="number"
              :min="field.min"
              :max="field.max"
              :step="field.step ?? 'any'"
              :value="displayNumberValue(field)"
              @focus="onNumberFocus(field)"
              @input="onNumberInput(field, $event)"
              @change="commitNumberField(field)"
              @blur="commitNumberField(field)"
            />

            <!-- 4. password -->
            <div v-else-if="field.type === 'password'" class="password-wrap">
              <input
                :id="`cfg-${field.key}`"
                class="input mono"
                :type="revealedPasswords[field.key] ? 'text' : 'password'"
                :value="String(settings.form[field.key] ?? '')"
                :placeholder="field.placeholder || '输入新密钥以更新'"
                autocomplete="new-password"
                @input="onStringInput(field, $event)"
              />
              <button
                type="button"
                class="btn sm"
                :aria-label="revealedPasswords[field.key] ? `隐藏 ${field.label}` : `显示 ${field.label}`"
                @click="togglePasswordReveal(field.key)"
              >
                {{ revealedPasswords[field.key] ? "隐藏密钥" : "显示密钥" }}
              </button>
            </div>

            <!-- 5. list -->
            <textarea
              v-else-if="field.type === 'list'"
              :id="`cfg-${field.key}`"
              class="textarea mono"
              rows="2"
              :value="displayListValue(field)"
              :placeholder="field.placeholder || '每行或用逗号分隔一项'"
              @focus="onListFocus(field)"
              @input="onListInput(field, $event)"
              @blur="commitListField(field)"
            ></textarea>

            <!-- 6. string -->
            <input
              v-else
              :id="`cfg-${field.key}`"
              class="input"
              type="text"
              :value="String(settings.form[field.key] ?? '')"
              :placeholder="field.placeholder || ''"
              @input="onStringInput(field, $event)"
            />

            <p v-if="field.type === 'password' && isSecretMaskedNow(field.key)" class="secret-guard-note">
              当前为脱敏掩码；未改动时保留服务端原密钥。
            </p>
            <p v-else-if="field.hint" class="field-hint">{{ field.hint }}</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.config-form-shell {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.tabular-nums {
  font-variant-numeric: tabular-nums;
}

.config-filter-bar {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--hairline);
}

.group-tabs {
  width: 100%;
}

.tab-dirty-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  color: var(--amber);
  background: var(--amber-soft);
  border: 1px solid color-mix(in srgb, var(--amber) 32%, transparent);
}

.config-search-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.config-search-box {
  flex: 1 1 280px;
  display: flex;
  gap: 8px;
  align-items: center;
}

.config-search-box .input {
  flex: 1;
  min-width: 0;
}

.dirty-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  user-select: none;
}

.dirty-toggle input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
}

.config-group-stack {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.config-group-section {
  scroll-margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--hairline);
}

.config-group-section:first-child {
  padding-top: 0;
  border-top: 0;
}

.group-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}

.group-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 680;
  color: var(--ink);
  letter-spacing: -0.01em;
}

.group-desc {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--muted);
  line-height: 1.5;
}

.config-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 18px;
  row-gap: 14px;
}

.config-field-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 0 12px;
  border-bottom: 1px solid var(--hairline);
}

.config-field-row.is-dirty {
  padding-left: 10px;
  border-left: 2px solid var(--amber);
}

@media (min-width: 900px) {
  .config-field-row.span-wide {
    grid-column: span 2;
  }
}

@media (max-width: 820px) {
  .config-field-grid {
    grid-template-columns: 1fr;
  }
}

.field-label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.field-label-row label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  white-space: normal;
  overflow: visible;
}

.field-badges {
  margin-top: 0;
  flex-shrink: 0;
}

.field-key {
  font-size: 11.5px;
  color: var(--muted);
  word-break: break-all;
}

.password-wrap {
  display: flex;
  gap: 8px;
  align-items: center;
}

.password-wrap .input {
  flex: 1;
  min-width: 0;
}

.field-hint {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.48;
}

.secret-guard-note {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--accent);
}
</style>
