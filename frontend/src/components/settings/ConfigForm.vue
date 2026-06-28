<script setup lang="ts">
// 运行配置表单：5 组字段，v-model 双向绑定到 useSettingsStore.form。
// 从 legacy renderConfigField/renderConfigForm（index.html 行 3080-3111）迁移为 Vue 模板。
// boolean → select（开启/关闭），number → input（min/max/step）。
import { CONFIG_FIELDS, type ConfigField } from "@/utils/configFields";
import { useSettingsStore } from "@/stores/settings";

const settings = useSettingsStore();

function asBoolean(value: number | boolean | undefined): boolean {
  return value === true;
}

function asNumber(value: number | boolean | string | undefined, fallback = 0): number {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function onField(field: ConfigField, event: Event): void {
  const target = event.target as HTMLSelectElement | HTMLInputElement;
  const raw = target.value;
  if (field.type === "boolean") {
    settings.form[field.key] = raw === "true";
  } else {
    settings.form[field.key] = asNumber(raw, field.min ?? 0);
  }
}
</script>

<template>
  <div class="settings-groups">
    <section v-for="group in CONFIG_FIELDS" :key="group.title" class="settings-group">
      <h3>{{ group.title }}</h3>
      <div class="settings-fields">
        <div v-for="field in group.fields" :key="field.key" class="field">
          <label :title="field.key">{{ field.label }}</label>
          <select
            v-if="field.type === 'boolean'"
            class="select"
            :value="asBoolean(settings.form[field.key]) ? 'true' : 'false'"
            @change="onField(field, $event)"
          >
            <option value="true">开启</option>
            <option value="false">关闭</option>
          </select>
          <input
            v-else
            class="input"
            type="number"
            :min="field.min"
            :max="field.max"
            :step="field.step"
            :value="asNumber(settings.form[field.key], field.min ?? 0)"
            @input="onField(field, $event)"
          />
        </div>
      </div>
    </section>
  </div>
</template>
