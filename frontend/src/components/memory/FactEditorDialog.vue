<script setup lang="ts">
// Schema v31 结构化事实新增/编辑弹窗：支持肯定/否定极性、6 大标准画像分区、稳定性、单值/多值基数、权威来源、置信度及一键撤回/恢复。
import { computed, onBeforeUnmount, ref, watch } from "vue";
import {
  createFactClaim,
  restoreFactClaim,
  retractFactClaim,
  updateFactClaim,
  type FactClaim,
} from "@/services/memoryAdminApi";
import {
  FACT_AUTHORITY_OPTIONS,
  FACT_CARDINALITY_OPTIONS,
  FACT_PROFILE_SECTIONS,
  FACT_STABILITY_OPTIONS,
} from "@/utils/memoryText";
import { fetchPersonRegistry } from "@/services/personApi";
import CandidateInput from "@/components/common/CandidateInput.vue";
import type { CandidateItem } from "@/stores/candidate";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { useLogsStore } from "@/stores/logs";
import { errText } from "@/utils/error";

const props = defineProps<{
  modelValue: boolean;
  editingClaim: FactClaim | null;
  defaultScopeType?: string;
  defaultScopeId?: string;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "saved"): void;
}>();

const graph = useGraphStore();
const app = useAppStore();
const logs = useLogsStore();

const isEdit = computed(() => Boolean(props.editingClaim?.claim_id));

const scopeType = ref<"person" | "chat">("person");
const scopeId = ref("");
const factKey = ref("");
const valueText = ref("");
const polarity = ref<"positive" | "negative">("positive");
const profileSection = ref("stable_facts");
const stability = ref("stable");
const cardinality = ref<"single" | "set">("set");
const authority = ref("manual");
const confidence = ref<number>(1);
const submitting = ref(false);

async function personIdSource(kw: string): Promise<CandidateItem[]> {
  if (scopeType.value !== "person") return [];
  try {
    const res = await fetchPersonRegistry(kw.trim(), 1, 20, graph.effectiveScope());
    return (res.items || [])
      .map((item) => ({
        value: item.person_id || item.display_name || item.person_name || "",
        note:
          (item.display_name || item.person_name) &&
          (item.display_name || item.person_name) !== item.person_id
            ? item.display_name || item.person_name || ""
            : "",
        kind: "人物",
      }))
      .filter((x) => x.value);
  } catch {
    return [];
  }
}

function close(): void {
  if (submitting.value) return;
  emit("update:modelValue", false);
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape" && !app.confirmation) {
    e.preventDefault();
    close();
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      submitting.value = false;
      const claim = props.editingClaim;
      if (claim) {
        scopeType.value = claim.scope_type === "chat" ? "chat" : "person";
        scopeId.value = claim.scope_id || "";
        factKey.value = claim.fact_key || "";
        valueText.value = claim.value_text || "";
        polarity.value = claim.polarity === "negative" ? "negative" : "positive";
        profileSection.value = claim.profile_section || "stable_facts";
        stability.value = claim.stability || "stable";
        cardinality.value = claim.cardinality === "single" ? "single" : "set";
        authority.value = claim.authority || "manual";
        confidence.value = Number.isFinite(Number(claim.confidence))
          ? Number(Number(claim.confidence).toFixed(2))
          : 1;
      } else {
        scopeType.value = props.defaultScopeType === "chat" ? "chat" : "person";
        scopeId.value = props.defaultScopeId || "";
        factKey.value = "";
        valueText.value = "";
        polarity.value = "positive";
        profileSection.value = "stable_facts";
        stability.value = "stable";
        cardinality.value = "set";
        authority.value = "manual";
        confidence.value = 1;
      }
      window.addEventListener("keydown", onKeydown);
    } else {
      window.removeEventListener("keydown", onKeydown);
    }
  },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});

async function submit(): Promise<void> {
  if (submitting.value) return;
  const sid = scopeId.value.trim();
  const fkey = factKey.value.trim();
  const val = valueText.value.trim();
  if (!sid) {
    app.pushError(
      scopeType.value === "person" ? "请填写人物标识 (person_id)" : "请填写会话标识 (scope_id)",
      "factEditor",
    );
    return;
  }
  if (!fkey || !val) {
    app.pushError("请完整填写「事实键 (fact_key)」与「事实陈述值」", "factEditor");
    return;
  }
  const conf = Math.max(0, Math.min(1, Number(confidence.value) || 0));

  submitting.value = true;
  const scope = graph.effectiveScope();
  try {
    if (isEdit.value && props.editingClaim) {
      await updateFactClaim(
        props.editingClaim.claim_id,
        {
          fact_key: fkey,
          value_text: val,
          polarity: polarity.value,
          profile_section: profileSection.value,
          stability: stability.value,
          cardinality: cardinality.value,
          authority: authority.value,
          confidence: conf,
        },
        scope,
      );
      logs.log(`结构化事实已更新：${fkey} = ${val}`, "info");
      app.pushSuccess?.("结构化事实已更新", "factEditor");
    } else {
      await createFactClaim(
        {
          scope_type: scopeType.value,
          scope_id: sid,
          fact_key: fkey,
          value_text: val,
          polarity: polarity.value,
          profile_section: profileSection.value,
          stability: stability.value,
          cardinality: cardinality.value,
          authority: authority.value,
          confidence: conf,
        },
        scope,
      );
      logs.log(`结构化事实已创建：${sid} · ${fkey} = ${val}`, "info");
      app.pushSuccess?.("新结构化事实已写入台账", "factEditor");
    }
    emit("saved");
    emit("update:modelValue", false);
  } catch (err) {
    app.pushError(errText(err), "saveFactClaim");
  } finally {
    submitting.value = false;
  }
}

async function toggleStatusInDialog(): Promise<void> {
  if (submitting.value || !props.editingClaim) return;
  const claim = props.editingClaim;
  submitting.value = true;
  const scope = graph.effectiveScope();
  try {
    if (claim.status === "retracted") {
      await restoreFactClaim(claim.claim_id, scope);
      app.pushSuccess?.("事实已恢复生效", "restoreFact");
    } else {
      await retractFactClaim(claim.claim_id, scope);
      app.pushSuccess?.("事实已撤回", "retractFact");
    }
    emit("saved");
    emit("update:modelValue", false);
  } catch (err) {
    app.pushError(errText(err), "toggleFactStatus");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="dialog-overlay" @click.self="close">
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="fact-dialog-title">
        <div class="panel-title">
          <h2 id="fact-dialog-title">
            {{ isEdit ? "编辑结构化事实" : "新增结构化事实" }}
          </h2>
          <button
            class="btn ghost sm"
            type="button"
            aria-label="关闭弹窗"
            :disabled="submitting"
            @click="close"
          >
            关闭
          </button>
        </div>

        <div class="form-grid-2">
          <label class="field">
            <span>归属类型 (scope_type)</span>
            <select v-model="scopeType" class="select" :disabled="isEdit || submitting">
              <option value="person">人物画像事实 (person)</option>
              <option value="chat">会话共识事实 (chat)</option>
            </select>
          </label>

          <div v-if="!isEdit && scopeType === 'person'">
            <CandidateInput
              v-model="scopeId"
              :source="personIdSource"
              :debounce-ms="180"
              label="归属对象 ID (scope_id)"
              placeholder="输入或选择人物 ID / 名称"
            />
          </div>
          <label v-else class="field">
            <span>归属对象 ID (scope_id)</span>
            <input
              v-model="scopeId"
              class="input"
              :disabled="isEdit || submitting"
              :placeholder="scopeType === 'person' ? '人物 person_id' : '会话/群聊 ID'"
            />
          </label>
        </div>

        <div class="form-grid-2">
          <label class="field">
            <span>事实属性键 (fact_key)</span>
            <input
              v-model="factKey"
              class="input"
              :disabled="submitting"
              placeholder="例如：职业 / 饮食偏好 / 生日 / 禁忌"
            />
          </label>

          <label class="field">
            <span>极性 (polarity)</span>
            <select v-model="polarity" class="select" :disabled="submitting">
              <option value="positive">肯定陈述（是 / 喜欢 / 具备）</option>
              <option value="negative">否定陈述（不是 / 讨厌 / 禁忌）</option>
            </select>
          </label>
        </div>

        <label class="field">
          <span>事实内容值 (value_text)</span>
          <textarea
            v-model="valueText"
            class="textarea"
            rows="2"
            :disabled="submitting"
            placeholder="例如：香菜（若极性选「否定陈述」，则语义为明确排斥/否定该值）"
          />
        </label>

        <div class="form-grid-2">
          <label class="field">
            <span>画像标准分区 (profile_section)</span>
            <select v-model="profileSection" class="select" :disabled="submitting">
              <option
                v-for="sec in FACT_PROFILE_SECTIONS"
                :key="sec.value"
                :value="sec.value"
              >
                {{ sec.label }}
              </option>
            </select>
          </label>

          <label class="field">
            <span>时效稳定性 (stability)</span>
            <select v-model="stability" class="select" :disabled="submitting">
              <option
                v-for="opt in FACT_STABILITY_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </label>
        </div>

        <div class="form-grid-3">
          <label class="field">
            <span>基数约束 (cardinality)</span>
            <select v-model="cardinality" class="select" :disabled="submitting">
              <option
                v-for="opt in FACT_CARDINALITY_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </label>

          <label class="field">
            <span>权威来源 (authority)</span>
            <select v-model="authority" class="select" :disabled="submitting">
              <option
                v-for="opt in FACT_AUTHORITY_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </label>

          <label class="field">
            <span>置信度 ({{ Number(confidence).toFixed(2) }})</span>
            <input
              v-model.number="confidence"
              class="input"
              type="number"
              min="0"
              max="1"
              step="0.05"
              :disabled="submitting"
            />
          </label>
        </div>

        <div class="dialog-footer">
          <div>
            <button
              v-if="isEdit && editingClaim"
              class="btn"
              :class="{ danger: editingClaim.status !== 'retracted' }"
              type="button"
              :disabled="submitting"
              @click="toggleStatusInDialog"
            >
              {{ editingClaim.status === "retracted" ? "恢复此事实" : "撤回此事实" }}
            </button>
          </div>
          <div class="dialog-actions">
            <button class="btn" type="button" :disabled="submitting" @click="close">取消</button>
            <button class="btn primary" type="button" :disabled="submitting" @click="submit">
              {{ submitting ? "保存中…" : isEdit ? "保存修改" : "录入事实" }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: var(--bg-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9000;
  padding: 16px;
}

.dialog {
  width: min(620px, 100%);
  max-height: 90vh;
  overflow-y: auto;
  padding: 20px 22px;
  background: var(--bg-elevated);
  color: var(--text);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.form-grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.form-grid-3 {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

@media (max-width: 640px) {
  .form-grid-2,
  .form-grid-3 {
    grid-template-columns: 1fr;
  }
}

.dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 6px;
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
}

.dialog-actions {
  display: flex;
  gap: 8px;
}
</style>
