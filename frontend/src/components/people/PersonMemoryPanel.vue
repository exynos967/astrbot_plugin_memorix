<script setup lang="ts">
import { ref, watch } from "vue";
import { usePeopleStore } from "@/stores/people";
import { useGraphStore } from "@/stores/graph";
import { useAppStore } from "@/stores/app";
import { errText } from "@/utils/error";
import { memoryAdmin, type AliasDetails, type FactClaim, type FactList } from "@/services/memoryAdminApi";

const people = usePeopleStore();
const graph = useGraphStore();
const app = useAppStore();
const aliases = ref("");
const derived = ref<string[]>([]);
const facts = ref<FactClaim[]>([]);
const busy = ref(false);
const editing = ref("");
const key = ref("");
const value = ref("");
const stability = ref("stable");
const cardinality = ref("set");
let revision = 0;

function resetEditor() {
  editing.value = ""; key.value = ""; value.value = "";
  stability.value = "stable"; cardinality.value = "set";
}

async function reload() {
  const seq = ++revision;
  const person = people.overrideId.trim();
  const scope = graph.effectiveScope();
  facts.value = []; aliases.value = ""; derived.value = [];
  if (!person) return;
  try {
    const [aliasResult, factResult] = await Promise.all([
      memoryAdmin<AliasDetails>("person/aliases", "get_aliases", { person_id: person }, scope),
      memoryAdmin<FactList>("facts", "list", { scope_type: "person", scope_id: person,
        statuses: ["active", "conflicted", "superseded", "retracted"], limit: 200 }, scope),
    ]);
    if (seq !== revision) return;
    aliases.value = (aliasResult.manual_aliases || []).join("\n");
    derived.value = aliasResult.derived_aliases || [];
    facts.value = factResult.items || [];
  } catch (error) {
    if (seq === revision) app.pushError(errText(error), "loadPersonMemory");
  }
}

async function mutate(endpoint: "facts" | "person/aliases", action: string, payload: Record<string, unknown>) {
  if (busy.value || !people.overrideId.trim()) return;
  const person = people.overrideId.trim();
  const scope = graph.effectiveScope();
  busy.value = true;
  try {
    await memoryAdmin(endpoint, action, payload, scope);
    if (person === people.overrideId.trim() && scope === graph.effectiveScope()) {
      resetEditor();
      await reload();
    }
  } catch (error) {
    app.pushError(errText(error), "editPersonMemory");
  } finally { busy.value = false; }
}

function saveAliases() {
  return mutate("person/aliases", "set_aliases", {
    person_id: people.overrideId.trim(), aliases: aliases.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
  });
}

function edit(fact: FactClaim) {
  editing.value = fact.claim_id; key.value = fact.fact_key; value.value = fact.value_text;
  stability.value = fact.stability; cardinality.value = fact.cardinality;
}

function saveFact() {
  return mutate("facts", editing.value ? "update" : "create", {
    claim_id: editing.value || undefined, scope_type: "person", scope_id: people.overrideId.trim(),
    fact_key: key.value.trim(), value_text: value.value.trim(), stability: stability.value,
    cardinality: cardinality.value, authority: "manual", reason: "astrbot_webui_manual_edit",
  });
}

watch(() => [people.overrideId, graph.effectiveScope()], () => { resetEditor(); void reload(); }, { immediate: true });
</script>

<template>
  <div class="band">
    <div class="panel-title"><h2>人物别名与事实</h2></div>
    <p>使用上方选定的 Person ID：{{ people.overrideId || "请先选择人物" }}</p>
    <fieldset :disabled="busy || !people.overrideId.trim()" class="memory-fields">
      <div class="field">
        <label for="manual-aliases">人工别名（每行一个，保存后优先使用）</label>
        <textarea id="manual-aliases" v-model="aliases" class="textarea" rows="3"></textarea>
        <p>自动别名：{{ derived.join("、") || "暂无" }}</p>
      </div>
      <div class="toolbar">
        <button class="btn primary" :disabled="!aliases.trim()" @click="saveAliases">保存别名</button>
        <button class="btn" @click="mutate('person/aliases', 'delete_aliases', { person_id: people.overrideId.trim() })">恢复自动别名</button>
        <button class="btn" @click="reload">刷新</button>
      </div>
      <h3>{{ editing ? "修改事实" : "添加事实" }}</h3>
      <div class="grid-2">
        <div class="field"><label for="fact-key">事实类别</label><input id="fact-key" v-model="key" class="input" placeholder="例如：饮食偏好" /></div>
        <div class="field"><label for="fact-value">事实内容</label><input id="fact-value" v-model="value" class="input" placeholder="例如：不吃香菜" /></div>
        <div class="field"><label for="fact-stability">稳定性</label><select id="fact-stability" v-model="stability" class="select"><option value="stable">稳定</option><option value="temporal">阶段性</option><option value="uncertain">待确认</option></select></div>
        <div class="field"><label for="fact-cardinality">同类事实</label><select id="fact-cardinality" v-model="cardinality" class="select" :disabled="!!editing"><option value="set">可同时存在多条</option><option value="single">只保留一个值</option></select></div>
      </div>
      <div class="toolbar">
        <button class="btn primary" :disabled="!key.trim() || !value.trim()" @click="saveFact">{{ editing ? "保存修改" : "添加事实" }}</button>
        <button v-if="editing" class="btn" @click="resetEditor">取消编辑</button>
      </div>
      <div class="fact-list">
        <article v-for="fact in facts" :key="fact.claim_id">
          <p><strong>{{ fact.fact_key }}</strong>：{{ fact.value_text }} <small>（{{ fact.status }} / {{ fact.stability }}）</small></p>
          <div class="toolbar">
            <button class="btn" @click="edit(fact)">编辑</button>
            <button v-if="['active', 'conflicted'].includes(fact.status)" class="btn danger" @click="mutate('facts', 'retract', { claim_id: fact.claim_id })">撤回</button>
            <button v-else class="btn" @click="mutate('facts', 'restore', { claim_id: fact.claim_id })">恢复</button>
          </div>
        </article>
        <p v-if="!facts.length">暂无事实记录。</p>
      </div>
    </fieldset>
  </div>
</template>

<style scoped>
.memory-fields { border: 0; padding: 0; margin: 0; min-width: 0; }
.fact-list article { padding: 10px 0; border-bottom: 1px solid var(--border-color, #8884); }
.toolbar { margin-top: 10px; }
</style>
