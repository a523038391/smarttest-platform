<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { fetchScripts } from '../services/automation'
import {
  loadHostExecutionPaths,
  rememberHostExecutionPaths,
} from '../services/execution-preferences'
import { fetchParameterEnums, fetchScriptParameters } from '../services/parameters'
import {
  createPlan,
  executePlan,
  fetchPlans,
  getTestPlanErrorMessage,
  updatePlan,
} from '../services/test-plans'
import type { AutomationScript } from '../types/automation'
import type {
  ExecutionBatch, ExecutionTarget, JsonRecord, PlanExecutionOptions,
  TestPlanItemInput, TestPlanResponse, TestPlanStatus,
} from '../types/test-plan'
import type {
  JsonScalar, ParameterEnumSet, ScriptParameterDefinition,
} from '../types/parameters'

interface PlanForm {
  name: string
  description: string
  maxParallel: number
  scriptIds: string[]
  parameterJson: Record<string, string>
}

const props = defineProps<{
  projectId: string
  projectsLoading: boolean
  projectsError: string
}>()
const emit = defineEmits<{ executed: [runIds: string[]] }>()

const statusInfo: Record<TestPlanStatus, { label: string; tone: string }> = {
  DRAFT: { label: '草稿', tone: 'neutral' },
  ACTIVE: { label: '已启用', tone: 'success' },
  ARCHIVED: { label: '已归档', tone: 'muted' },
}
const plans = ref<TestPlanResponse[]>([])
const scripts = ref<AutomationScript[]>([])
const parameterEnums = ref<ParameterEnumSet[]>([])
const scriptParameterDefinitions = ref<Record<string, ScriptParameterDefinition[]>>({})
const loading = ref(false)
const listError = ref('')
const showForm = ref(false)
const submitting = ref(false)
const formError = ref('')
const activatingId = ref('')
const executingId = ref('')
const actionError = ref('')
const executionResults = ref<Record<string, ExecutionBatch>>({})
const editingPlan = ref<TestPlanResponse | null>(null)
const copyingPlan = ref<TestPlanResponse | null>(null)
const executionPlan = ref<TestPlanResponse | null>(null)
const executionTarget = ref<ExecutionTarget>('docker')
const hostProjectDirectory = ref('')
const hostPythonExecutable = ref('')
const executionError = ref('')
const form = ref<PlanForm>({
  name: '', description: '', maxParallel: 10, scriptIds: [], parameterJson: {},
})
let controller: AbortController | null = null

const activeScripts = computed(() => scripts.value.filter((script) => script.status === 'ACTIVE'))
const selectedFormScripts = computed(() => activeScripts.value.filter(
  (script) => form.value.scriptIds.includes(script.id),
))
const hostExecutionAvailable = computed(() => executionPlan.value !== null
  && executionPlan.value.items.every((item) => {
    const script = scripts.value.find((candidate) => candidate.id === item.script_id)
    return script?.engine === 'pytest' || script?.engine === 'playwright'
  }))

watch(hostExecutionAvailable, (available) => {
  if (!available && executionTarget.value === 'host') executionTarget.value = 'docker'
})

async function loadData() {
  controller?.abort()
  if (!props.projectId) {
    plans.value = []
    scripts.value = []
    parameterEnums.value = []
    scriptParameterDefinitions.value = {}
    loading.value = false
    return
  }
  const currentController = new AbortController()
  controller = currentController
  loading.value = true
  listError.value = ''
  try {
    const [loadedPlans, loadedScripts, loadedEnums] = await Promise.all([
      fetchPlans(props.projectId, currentController.signal),
      fetchScripts(props.projectId, currentController.signal),
      fetchParameterEnums(props.projectId, currentController.signal),
    ])
    const loadedDefinitions = await Promise.all(loadedScripts
      .filter((script) => script.status === 'ACTIVE')
      .map(async (script) => [
        script.id,
        await fetchScriptParameters(script.id, currentController.signal),
      ] as const))
    plans.value = loadedPlans
    scripts.value = loadedScripts
    parameterEnums.value = loadedEnums
    scriptParameterDefinitions.value = Object.fromEntries(loadedDefinitions.map(
      ([scriptId, definitions]) => [
        scriptId, [...definitions].sort((left, right) => left.position - right.position),
      ],
    ))
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    listError.value = getTestPlanErrorMessage(reason)
  } finally {
    if (controller === currentController) loading.value = false
  }
}

watch(() => props.projectId, () => {
  showForm.value = false
  executionPlan.value = null
  executionResults.value = {}
  void loadData()
}, { immediate: true })

onBeforeUnmount(() => controller?.abort())

function openCreateForm() {
  editingPlan.value = null
  copyingPlan.value = null
  form.value = {
    name: '', description: '', maxParallel: 10, scriptIds: [], parameterJson: {},
  }
  formError.value = ''
  showForm.value = true
}

function parameterDefinitions(scriptId: string): ScriptParameterDefinition[] {
  return scriptParameterDefinitions.value[scriptId] ?? []
}

function parameterEnum(definition: ScriptParameterDefinition): ParameterEnumSet | undefined {
  return parameterEnums.value.find((item) => item.id === definition.enum_set_id)
}

function parameterObjectById(scriptId: string): JsonRecord | null {
  const source = form.value.parameterJson[scriptId]?.trim() || '{}'
  try {
    const parsed: unknown = JSON.parse(source)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null
    return parsed as JsonRecord
  } catch {
    return null
  }
}

function initializeScriptParameters(scriptId: string) {
  const parameters = parameterObjectById(scriptId)
  if (parameters === null) return
  const next = { ...parameters }
  let changed = false
  for (const definition of parameterDefinitions(scriptId)) {
    if (!Object.prototype.hasOwnProperty.call(next, definition.name)) {
      next[definition.name] = definition.default_value
      changed = true
    }
  }
  if (changed) form.value.parameterJson[scriptId] = JSON.stringify(next, null, 2)
}

function initializeSelectedScriptParameters() {
  for (const scriptId of form.value.scriptIds) initializeScriptParameters(scriptId)
}

function handleScriptSelection(script: AutomationScript, event: Event) {
  if ((event.target as HTMLInputElement).checked) initializeScriptParameters(script.id)
}

function openEditForm(plan: TestPlanResponse) {
  editingPlan.value = plan
  copyingPlan.value = null
  form.value = {
    name: plan.name,
    description: plan.description,
    maxParallel: plan.max_parallel,
    scriptIds: plan.items.map((item) => item.script_id),
    parameterJson: Object.fromEntries(plan.items.map((item) => [
      item.script_id, JSON.stringify(item.default_parameters, null, 2),
    ])),
  }
  initializeSelectedScriptParameters()
  formError.value = ''
  showForm.value = true
}

function copiedPlanName(name: string): string {
  const existing = new Set(plans.value.map((plan) => plan.name))
  for (let number = 1; number <= plans.value.length + 1; number += 1) {
    const suffix = number === 1 ? '（副本）' : `（副本 ${number}）`
    const candidate = `${name.slice(0, 255 - suffix.length)}${suffix}`
    if (!existing.has(candidate)) return candidate
  }
  return `${name.slice(0, 245)}（新副本）`
}

function canCopyPlan(plan: TestPlanResponse): boolean {
  return plan.items.every((item) => activeScripts.value.some(
    (script) => script.id === item.script_id,
  ))
}

function openCopyForm(plan: TestPlanResponse) {
  if (!canCopyPlan(plan)) return
  editingPlan.value = null
  copyingPlan.value = plan
  form.value = {
    name: copiedPlanName(plan.name),
    description: plan.description,
    maxParallel: plan.max_parallel,
    scriptIds: plan.items.map((item) => item.script_id),
    parameterJson: Object.fromEntries(plan.items.map((item) => [
      item.script_id, JSON.stringify(item.default_parameters, null, 2),
    ])),
  }
  initializeSelectedScriptParameters()
  formError.value = ''
  showForm.value = true
}

function closeForm() {
  if (!submitting.value) {
    showForm.value = false
    editingPlan.value = null
    copyingPlan.value = null
  }
}

function parameterObject(script: AutomationScript): JsonRecord | null {
  return parameterObjectById(script.id)
}

function parameterValue(scriptId: string, name: string): unknown {
  return parameterObjectById(scriptId)?.[name]
}

function inputParameterValue(scriptId: string, name: string): string | number {
  const value = parameterValue(scriptId, name)
  return typeof value === 'string' || typeof value === 'number' ? value : ''
}

function booleanParameterValue(scriptId: string, name: string): string {
  const value = parameterValue(scriptId, name)
  if (value === true) return 'true'
  if (value === false) return 'false'
  return value === null || value === undefined ? '' : 'invalid'
}

function enumParameterValue(scriptId: string, definition: ScriptParameterDefinition): string {
  const enumSet = parameterEnum(definition)
  const value = parameterValue(scriptId, definition.name)
  const index = enumSet?.options.findIndex((option) => Object.is(option.value, value)) ?? -1
  return index >= 0 ? `option-${index}` : 'unmatched'
}

function updateParameterValue(scriptId: string, name: string, value: JsonScalar) {
  const parameters = parameterObjectById(scriptId)
  if (parameters === null) return
  form.value.parameterJson[scriptId] = JSON.stringify({
    ...parameters,
    [name]: value,
  }, null, 2)
}

function updateTextParameter(scriptId: string, name: string, event: Event) {
  updateParameterValue(scriptId, name, (event.target as HTMLInputElement).value)
}

function updateNumberParameter(scriptId: string, name: string, event: Event) {
  const source = (event.target as HTMLInputElement).value
  if (!source) {
    updateParameterValue(scriptId, name, null)
    return
  }
  const value = Number(source)
  if (Number.isFinite(value)) updateParameterValue(scriptId, name, value)
}

function updateBooleanParameter(scriptId: string, name: string, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  updateParameterValue(scriptId, name, value === '' ? null : value === 'true')
}

function updateEnumParameter(
  scriptId: string, definition: ScriptParameterDefinition, event: Event,
) {
  const index = Number((event.target as HTMLSelectElement).value.replace('option-', ''))
  const option = parameterEnum(definition)?.options[index]
  if (option) updateParameterValue(scriptId, definition.name, option.value)
}

function parameterSummary(parameters: JsonRecord): string {
  const keys = Object.keys(parameters)
  return keys.length === 0 ? '无执行入参' : `${keys.length} 个入参：${keys.slice(0, 3).join('、')}${keys.length > 3 ? '…' : ''}`
}

async function submitForm() {
  const name = form.value.name.trim()
  formError.value = ''
  if (!name || form.value.scriptIds.length === 0) {
    formError.value = '请填写计划名称，并至少选择一个已启用脚本。'
    return
  }
  if (!Number.isInteger(form.value.maxParallel)
    || form.value.maxParallel < 1 || form.value.maxParallel > 100) {
    formError.value = '最大并发数必须是 1 到 100 之间的整数。'
    return
  }
  const selected = activeScripts.value.filter((script) => form.value.scriptIds.includes(script.id))
  if (selected.length !== form.value.scriptIds.length) {
    formError.value = '所选脚本状态已变化，请关闭表单后刷新。'
    return
  }
  const parsedParameters = new Map<string, JsonRecord>()
  for (const script of selected) {
    const parameters = parameterObject(script)
    if (parameters === null) {
      formError.value = `“${script.name}”的执行入参必须是有效的 JSON 对象。`
      return
    }
    for (const definition of parameterDefinitions(script.id)) {
      const value = parameters[definition.name]
      if (definition.required && (value === null || value === undefined || value === '')) {
        formError.value = `“${script.name}”的必填入参“${definition.label || definition.name}”不能为空。`
        return
      }
      if (value !== null && value !== undefined) {
        const validType = definition.input_type === 'text' ? typeof value === 'string'
          : definition.input_type === 'number' ? typeof value === 'number' && Number.isFinite(value)
            : definition.input_type === 'integer' ? Number.isInteger(value)
              : definition.input_type === 'boolean' ? typeof value === 'boolean'
                : parameterEnum(definition)?.options.some((option) => Object.is(option.value, value)) ?? false
        if (!validType) {
          formError.value = `“${script.name}”的入参“${definition.label || definition.name}”类型或枚举值无效。`
          return
        }
      }
    }
    parsedParameters.set(script.id, parameters)
  }
  submitting.value = true
  try {
    const items: TestPlanItemInput[] = selected.map((script) => {
      const sourcePlan = editingPlan.value ?? copyingPlan.value
      const existing = sourcePlan?.items.find((item) => item.script_id === script.id)
      return {
        item_id: editingPlan.value ? existing?.item_id : undefined,
        script_id: script.id,
        script_revision: existing?.script_revision ?? script.revision,
        environment_id: existing?.environment_id,
        environment_revision: existing?.environment_revision,
        default_parameters: parsedParameters.get(script.id) ?? {},
        data_rows: existing?.data_rows,
        execution_policy: existing?.execution_policy,
      }
    })
    if (editingPlan.value) {
      const updated = await updatePlan(editingPlan.value.id, {
        name,
        description: form.value.description.trim(),
        max_parallel: form.value.maxParallel,
        items,
        status: editingPlan.value.status,
        state_version: editingPlan.value.state_version,
      })
      plans.value = plans.value.map((plan) => plan.id === updated.id ? updated : plan)
    } else {
      const created = await createPlan({
        project_id: props.projectId,
        name,
        description: form.value.description.trim(),
        max_parallel: form.value.maxParallel,
        items,
      })
      plans.value = [...plans.value, created]
    }
    showForm.value = false
    editingPlan.value = null
    copyingPlan.value = null
  } catch (reason) {
    formError.value = getTestPlanErrorMessage(reason)
  } finally {
    submitting.value = false
  }
}

async function activate(plan: TestPlanResponse) {
  if (activatingId.value) return
  activatingId.value = plan.id
  actionError.value = ''
  try {
    const updated = await updatePlan(plan.id, {
      name: plan.name,
      description: plan.description,
      items: plan.items,
      max_parallel: plan.max_parallel,
      status: 'ACTIVE',
      state_version: plan.state_version,
    })
    plans.value = plans.value.map((item) => item.id === updated.id ? updated : item)
  } catch (reason) {
    actionError.value = getTestPlanErrorMessage(reason)
  } finally {
    activatingId.value = ''
  }
}

function openExecution(plan: TestPlanResponse) {
  if (executingId.value) return
  const paths = loadHostExecutionPaths()
  executionPlan.value = plan
  executionTarget.value = 'docker'
  hostProjectDirectory.value = paths.projectDirectory
  hostPythonExecutable.value = paths.pythonExecutable
  executionError.value = ''
  actionError.value = ''
}

function closeExecution() {
  if (executingId.value) return
  executionPlan.value = null
  executionTarget.value = 'docker'
  hostProjectDirectory.value = ''
  hostPythonExecutable.value = ''
  executionError.value = ''
}

async function execute() {
  const plan = executionPlan.value
  if (!plan || executingId.value) return
  let options: PlanExecutionOptions = { execution_target: 'docker' }
  executionError.value = ''
  if (executionTarget.value === 'host') {
    if (!hostExecutionAvailable.value) {
      executionError.value = 'Windows 本机执行仅支持全部脚本均为 pytest 或 Playwright 的计划。'
      return
    }
    const projectDirectory = hostProjectDirectory.value.trim()
    const pythonExecutable = hostPythonExecutable.value.trim()
    if (!projectDirectory || !pythonExecutable) {
      executionError.value = 'Windows 本机执行需要填写项目目录和 Python 解释器路径。'
      return
    }
    options = {
      execution_target: 'host',
      project_directory: projectDirectory,
      python_executable: pythonExecutable,
    }
  }
  executingId.value = plan.id
  try {
    const batch = await executePlan(plan.id, options)
    if (options.execution_target === 'host') rememberHostExecutionPaths(options)
    executionResults.value = { ...executionResults.value, [plan.id]: batch }
    executionPlan.value = null
    emit('executed', batch.run_ids)
  } catch (reason) {
    executionError.value = getTestPlanErrorMessage(reason)
  } finally {
    executingId.value = ''
  }
}

function scriptName(scriptId: string): string {
  return scripts.value.find((script) => script.id === scriptId)?.name ?? scriptId
}
</script>

<template>
  <section class="plans" aria-labelledby="plans-title">
    <header class="plans-header">
      <div><h2 id="plans-title">测试计划</h2><p>固定已启用脚本修订，启用计划后即可一键生成执行批次。</p></div>
      <button class="primary-button" type="button" :disabled="!projectId || projectsLoading || activeScripts.length === 0" @click="openCreateForm">新建计划</button>
    </header>

    <div v-if="projectsLoading" class="state-view" role="status"><span class="loader"></span><strong>正在加载项目</strong></div>
    <div v-else-if="projectsError" class="state-view offline-view"><span class="offline-icon">!</span><strong>项目数据不可用</strong><p>{{ projectsError }}</p></div>
    <div v-else-if="!projectId" class="state-view"><span class="empty-icon">◇</span><strong>请先选择或新建项目</strong><p>在右上角选择项目后再管理测试计划。</p></div>
    <template v-else>
      <div v-if="loading" class="state-view" role="status"><span class="loader"></span><strong>正在读取计划与脚本</strong></div>
      <div v-else-if="listError" class="state-view offline-view"><span class="offline-icon">!</span><strong>计划数据暂时不可用</strong><p>{{ listError }}</p><button type="button" @click="loadData">重新连接</button></div>
      <template v-else>
        <div v-if="activeScripts.length === 0" class="notice"><strong>暂无已启用脚本</strong><span>请先在“自动化”中激活至少一个脚本，再新建测试计划。</span></div>
        <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
        <div v-if="plans.length === 0" class="state-view"><span class="empty-icon">◇</span><strong>暂无测试计划</strong><p>{{ activeScripts.length ? '点击“新建计划”，选择一个或多个已启用脚本。' : '启用自动化脚本后即可创建第一个测试计划。' }}</p></div>
        <div v-else class="plan-grid">
          <article v-for="plan in plans" :key="plan.id" class="plan-card">
            <header><div><h3>{{ plan.name }}</h3><p v-if="plan.description">{{ plan.description }}</p></div><span class="badge" :class="`tone-${statusInfo[plan.status].tone}`">{{ statusInfo[plan.status].label }}</span></header>
            <dl><div><dt>脚本数</dt><dd>{{ plan.items.length }}</dd></div><div><dt>计划修订</dt><dd>v{{ plan.revision }}</dd></div><div><dt>最大并发</dt><dd>{{ plan.max_parallel }}</dd></div></dl>
            <ul class="script-list"><li v-for="item in plan.items" :key="item.item_id"><span><strong>{{ scriptName(item.script_id) }}</strong><small>{{ parameterSummary(item.default_parameters) }}</small></span><small>脚本 v{{ item.script_revision }}</small></li></ul>
            <div v-if="executionResults[plan.id]" class="batch-result"><strong>批次 {{ executionResults[plan.id]?.id }}</strong><span>已创建 {{ executionResults[plan.id]?.run_ids.length }} 个 Run</span></div>
            <footer>
              <button class="secondary-button" type="button" :disabled="!canCopyPlan(plan) || Boolean(activatingId) || Boolean(executingId)" :title="canCopyPlan(plan) ? '复制为新的草稿计划' : '计划包含未启用脚本，暂时无法复制'" @click="openCopyForm(plan)">复制创建</button>
              <button v-if="plan.status !== 'ARCHIVED'" class="secondary-button" type="button" :disabled="Boolean(activatingId) || Boolean(executingId)" @click="openEditForm(plan)">编辑入参</button>
              <button v-if="plan.status === 'DRAFT'" class="link-button" type="button" :disabled="Boolean(activatingId)" @click="activate(plan)">{{ activatingId === plan.id ? '启用中…' : '启用' }}</button>
              <button v-else-if="plan.status === 'ACTIVE'" class="primary-button execute-button" type="button" :disabled="Boolean(executingId)" @click="openExecution(plan)">{{ executingId === plan.id ? '创建批次中…' : '一键执行' }}</button>
              <span v-else class="readonly-label">已归档，只读</span>
            </footer>
          </article>
        </div>
      </template>
    </template>

    <div v-if="showForm" class="form-overlay" role="dialog" :aria-label="editingPlan ? '编辑测试计划' : copyingPlan ? '复制测试计划' : '新建测试计划'">
      <div class="form-panel">
        <header><h3>{{ editingPlan ? '编辑测试计划与入参' : copyingPlan ? '复制测试计划' : '新建测试计划' }}</h3><button class="close-button" type="button" :disabled="submitting" aria-label="关闭" @click="closeForm">×</button></header>
        <div class="form-body">
          <label class="form-field"><span>计划名称</span><input v-model="form.name" type="text" maxlength="255" :disabled="submitting" placeholder="例如：每日冒烟计划"></label>
          <label class="form-field"><span>描述（可选）</span><textarea v-model="form.description" rows="3" maxlength="100000" :disabled="submitting"></textarea></label>
          <label class="form-field"><span>最大并发数</span><input v-model.number="form.maxParallel" type="number" min="1" max="100" :disabled="submitting"></label>
          <fieldset><legend>选择已启用脚本（至少一个）</legend><label v-for="script in activeScripts" :key="script.id" class="script-option"><input v-model="form.scriptIds" type="checkbox" :value="script.id" :disabled="submitting" @change="handleScriptSelection(script, $event)"><span><strong>{{ script.name }}</strong><small>{{ script.engine }} · 当前修订 v{{ script.revision }}</small></span></label></fieldset>
          <section v-if="selectedFormScripts.length" class="parameters-section">
            <h4>执行入参</h4>
            <div v-for="script in selectedFormScripts" :key="script.id" class="parameter-editor">
              <div class="parameter-editor-title"><strong>{{ script.name }}</strong><small>JSON 对象</small></div>
              <template v-if="parameterDefinitions(script.id).length">
                <div class="parameter-grid">
                  <div v-for="definition in parameterDefinitions(script.id)" :key="definition.name" class="parameter-row">
                    <label class="parameter-label" :for="`parameter-${script.id}-${definition.position}`">
                      <span>{{ definition.label || definition.name }}<b v-if="definition.required" title="必填"> *</b></span>
                      <small>{{ definition.name }}</small>
                    </label>
                    <div class="parameter-control">
                      <select
                        v-if="definition.input_type === 'enum'"
                        :id="`parameter-${script.id}-${definition.position}`"
                        :value="enumParameterValue(script.id, definition)"
                        :disabled="submitting || parameterObject(script) === null || !parameterEnum(definition)"
                        @change="updateEnumParameter(script.id, definition, $event)"
                      >
                        <option v-if="enumParameterValue(script.id, definition) === 'unmatched'" value="unmatched" disabled>请选择</option>
                        <option v-for="(option, index) in parameterEnum(definition)?.options ?? []" :key="index" :value="`option-${index}`">{{ option.label }}</option>
                      </select>
                      <input
                        v-else-if="definition.input_type === 'text'"
                        :id="`parameter-${script.id}-${definition.position}`"
                        type="text"
                        :value="inputParameterValue(script.id, definition.name)"
                        :disabled="submitting || parameterObject(script) === null"
                        @input="updateTextParameter(script.id, definition.name, $event)"
                      >
                      <input
                        v-else-if="definition.input_type === 'number' || definition.input_type === 'integer'"
                        :id="`parameter-${script.id}-${definition.position}`"
                        type="number"
                        :step="definition.input_type === 'integer' ? 1 : 'any'"
                        :value="inputParameterValue(script.id, definition.name)"
                        :disabled="submitting || parameterObject(script) === null"
                        @input="updateNumberParameter(script.id, definition.name, $event)"
                      >
                      <select
                        v-else
                        :id="`parameter-${script.id}-${definition.position}`"
                        :value="booleanParameterValue(script.id, definition.name)"
                        :disabled="submitting || parameterObject(script) === null"
                        @change="updateBooleanParameter(script.id, definition.name, $event)"
                      >
                        <option value="">未设置</option>
                        <option value="true">是</option>
                        <option value="false">否</option>
                        <option v-if="booleanParameterValue(script.id, definition.name) === 'invalid'" value="invalid" disabled>当前值无效</option>
                      </select>
                      <small v-if="definition.input_type === 'enum' && !parameterEnum(definition)" class="enum-warning" role="alert">引用的枚举不存在，请使用高级 JSON 编辑。</small>
                    </div>
                  </div>
                </div>
                <p v-if="parameterObject(script) === null" class="json-warning" role="alert">JSON 内容无效，请先在高级编辑中修正。</p>
                <details class="advanced-json">
                  <summary>高级：直接编辑 JSON</summary>
                  <label class="form-field">
                    <span>{{ script.name }}（JSON 对象）</span>
                    <textarea v-model="form.parameterJson[script.id]" rows="7" spellcheck="false" :disabled="submitting" placeholder='{"shopId":162,"shopAccount":"LIPENG_US","warehouseId":129,"companyId":2}'></textarea>
                    <small>会保留结构化参数之外的额外 JSON 字段。</small>
                  </label>
                </details>
              </template>
              <label v-else class="form-field">
                <span>{{ script.name }}（JSON 对象）</span>
                <textarea v-model="form.parameterJson[script.id]" rows="7" spellcheck="false" :disabled="submitting" placeholder='{"shopId":162,"shopAccount":"LIPENG_US","warehouseId":129,"companyId":2}'></textarea>
                <small>脚本通过 runner.load_parameters() 读取；数字不要加引号，文本需要加双引号。</small>
              </label>
            </div>
          </section>
          <p class="form-hint">{{ copyingPlan ? '保存后创建新的草稿计划，原计划保持不变。' : '保存会生成新的计划修订；历史执行参数不变，后续执行使用最新入参。' }}</p>
          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
        </div>
        <footer><button class="secondary-button" type="button" :disabled="submitting" @click="closeForm">取消</button><button class="primary-button" type="button" :disabled="submitting" @click="submitForm">{{ submitting ? '保存中…' : editingPlan ? '保存草稿与入参' : copyingPlan ? '创建计划副本' : '创建草稿计划' }}</button></footer>
      </div>
    </div>

    <div v-if="executionPlan" class="form-overlay" role="dialog" aria-label="选择执行环境">
      <div class="form-panel execution-panel">
        <header><h3>执行“{{ executionPlan.name }}”</h3><button class="close-button" type="button" :disabled="Boolean(executingId)" aria-label="关闭" @click="closeExecution">×</button></header>
        <div class="form-body">
          <label class="form-field">
            <span>执行环境</span>
            <select v-model="executionTarget" :disabled="Boolean(executingId)">
              <option value="docker">Docker 隔离环境</option>
              <option value="host" :disabled="!hostExecutionAvailable">Windows 本机执行</option>
            </select>
          </label>
          <p class="execution-hint">本次选择仅适用于当前执行批次，不会保存到脚本或测试计划。</p>
          <p v-if="!hostExecutionAvailable" class="host-unavailable">Windows 本机执行不可用：计划中的所有脚本必须均为 pytest 或 Playwright。</p>
          <template v-if="executionTarget === 'host'">
            <label class="form-field"><span>Windows 项目目录</span><input v-model="hostProjectDirectory" type="text" maxlength="2048" :disabled="Boolean(executingId)" placeholder="D:\automation\my-project"></label>
            <label class="form-field"><span>Python 解释器</span><input v-model="hostPythonExecutable" type="text" maxlength="2048" :disabled="Boolean(executingId)" placeholder="D:\automation\my-project\.venv\Scripts\python.exe"></label>
          </template>
          <p v-if="executionError" class="form-error" role="alert">{{ executionError }}</p>
        </div>
        <footer><button class="secondary-button" type="button" :disabled="Boolean(executingId)" @click="closeExecution">取消</button><button class="primary-button" type="button" :disabled="Boolean(executingId)" @click="execute">{{ executingId ? '创建批次中…' : '确认执行' }}</button></footer>
      </div>
    </div>
  </section>
</template>

<style scoped>
.plans{display:flex;flex-direction:column;gap:18px}.plans-header{display:flex;align-items:center;justify-content:space-between;gap:16px}.plans-header h2{margin:0;color:#1c273a;font-size:20px}.plans-header p{margin:6px 0 0;color:#8994a7;font-size:12px}.primary-button{height:40px;padding:0 18px;border:0;border-radius:9px;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc);font-size:13px;font-weight:700;cursor:pointer}.primary-button:disabled{cursor:not-allowed;opacity:.6}.secondary-button{height:36px;padding:0 14px;border:1px solid #e2e7ef;border-radius:8px;color:#556176;background:#fff;font-size:12px;font-weight:600;cursor:pointer}.state-view{min-height:220px;border:1px solid #e7ecf3;border-radius:14px;background:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:6px;padding:24px}.state-view strong{color:#28354a;font-size:15px}.state-view p{max-width:440px;margin:0;color:#8d98aa;font-size:12px;line-height:1.7}.state-view button{margin-top:8px}.offline-view{border-color:#ffd7db}.offline-icon{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:#fff;background:#e06070;font-weight:800}.empty-icon{font-size:22px;color:#5d80dc}.loader{width:20px;height:20px;border:3px solid #dbe4f5;border-top-color:#4f72ca;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.notice{display:flex;gap:10px;align-items:center;padding:12px 15px;border:1px solid #dbe5fa;border-radius:10px;color:#657493;background:#f5f8ff;font-size:12px}.notice strong{color:#3f5fae}.action-error,.form-error{margin:0;padding:10px 12px;border:1px solid #ffd7db;border-radius:8px;color:#b84350;background:#fff5f6;font-size:12px}.plan-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}.plan-card{padding:18px;border:1px solid #e7ecf3;border-radius:14px;background:#fff}.plan-card>header{display:flex;justify-content:space-between;gap:12px}.plan-card h3{margin:0;color:#1c273a;font-size:15px}.plan-card header p{margin:5px 0 0;color:#8994a7;font-size:11px}.badge{height:max-content;padding:3px 9px;border-radius:6px;font-size:11px;font-weight:700;white-space:nowrap}.tone-neutral{color:#70809a;background:#f1f4f8}.tone-success{color:#1c8a5a;background:#e5f7ee}.tone-muted{color:#8994a7;background:#f1f4f8}.plan-card dl{display:grid;grid-template-columns:repeat(3,1fr);margin:16px 0;padding:12px;border-radius:9px;background:#f8fafc}.plan-card dl div{display:grid;gap:3px;text-align:center}.plan-card dt{color:#929daf;font-size:10px}.plan-card dd{margin:0;color:#344158;font-size:13px;font-weight:700}.script-list{display:grid;gap:7px;margin:0;padding:0;list-style:none}.script-list li{display:flex;justify-content:space-between;gap:8px;color:#556176;font-size:12px}.script-list li>span{display:grid;gap:2px}.script-list li strong{font-size:12px}.script-list small{color:#8994a7;font-size:10px}.plan-card>footer{display:flex;justify-content:flex-end;align-items:center;gap:9px;min-height:40px;margin-top:16px;border-top:1px solid #eef1f6;padding-top:12px}.link-button{border:0;background:none;color:#4f72ca;font-size:12px;font-weight:700;cursor:pointer}.execute-button{height:36px}.readonly-label{color:#99a2b1;font-size:11px}.batch-result{display:grid;gap:4px;margin-top:13px;padding:9px;border-radius:8px;color:#247451;background:#eaf8f1;font-size:10px;overflow-wrap:anywhere}.form-overlay{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(20,28,45,.4)}.form-panel{width:min(620px,100%);max-height:90vh;overflow-y:auto;border-radius:16px;background:#fff;box-shadow:0 24px 60px rgba(20,28,45,.3)}.form-panel>header{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid #eef1f6}.form-panel h3{margin:0;color:#1c273a;font-size:16px}.close-button{width:30px;height:30px;border:0;border-radius:8px;background:#f1f4f8;color:#556176;font-size:16px;cursor:pointer}.form-body{display:grid;gap:14px;padding:20px 24px}.form-field{display:grid;gap:6px;color:#556176;font-size:12px}.form-field input,.form-field textarea{padding:0 12px;border:1px solid #dfe5ee;border-radius:8px;color:#253044;background:#fbfcfe;font:500 13px inherit}.form-field input{height:40px}.form-field textarea{padding-top:10px;resize:vertical}.form-field small{color:#8994a7;font-size:10px}.parameter-editor textarea{font-family:Consolas,monospace;line-height:1.5}.parameters-section{display:grid;gap:12px;padding:14px;border:1px solid #dbe5fa;border-radius:10px;background:#f8faff}.parameters-section h4{margin:0;color:#3f5fae;font-size:13px}fieldset{display:grid;gap:8px;margin:0;padding:12px;border:1px solid #e1e6ef;border-radius:9px}legend{padding:0 5px;color:#556176;font-size:12px}.script-option{display:flex;align-items:center;gap:9px;padding:8px;border-radius:7px;background:#f8fafc}.script-option span{display:grid;gap:2px}.script-option strong{color:#344158;font-size:12px}.script-option small,.form-hint{color:#8994a7;font-size:10px}.form-hint{margin:-4px 0 0}.form-panel>footer{display:flex;justify-content:flex-end;gap:10px;padding:16px 24px;border-top:1px solid #eef1f6}@media(max-width:650px){.plans-header{align-items:flex-start}.notice{align-items:flex-start;flex-direction:column}.plan-grid{grid-template-columns:1fr}}
.form-field select{height:40px;padding:0 12px;border:1px solid #dfe5ee;border-radius:8px;color:#253044;background:#fbfcfe;font:500 13px inherit}.parameter-editor{display:grid;gap:10px;padding:12px;border:1px solid #e3e9f4;border-radius:9px;background:#fff}.parameter-editor-title{display:flex;align-items:center;justify-content:space-between;gap:10px;color:#344158;font-size:12px}.parameter-editor-title small{color:#8994a7;font-size:10px;font-weight:500}.parameter-grid{display:grid;border:1px solid #e5eaf2;border-radius:8px;overflow:hidden}.parameter-row{display:grid;grid-template-columns:minmax(130px,1fr) minmax(180px,1.5fr);gap:12px;align-items:center;padding:10px 12px;background:#fbfcfe}.parameter-row+.parameter-row{border-top:1px solid #e5eaf2}.parameter-label{display:grid;gap:2px;color:#46546b;font-size:12px}.parameter-label b{color:#d54f5d}.parameter-label small{color:#929daf;font:10px Consolas,monospace}.parameter-control{display:grid;gap:5px}.parameter-control input,.parameter-control select{width:100%;height:36px;box-sizing:border-box;padding:0 10px;border:1px solid #dfe5ee;border-radius:7px;color:#253044;background:#fff;font:500 12px inherit}.parameter-control :disabled{cursor:not-allowed;background:#f3f5f8;color:#929daf}.enum-warning,.json-warning{color:#a36b17;font-size:10px;line-height:1.5}.json-warning{margin:0;padding:8px 10px;border-radius:7px;background:#fff8e8}.advanced-json{border-top:1px solid #edf0f5;padding-top:9px}.advanced-json summary{width:max-content;color:#4f72ca;font-size:11px;font-weight:700;cursor:pointer}.advanced-json[open] summary{margin-bottom:10px}.advanced-json textarea{font-family:Consolas,monospace;line-height:1.5}.execution-panel{width:min(520px,100%)}.execution-hint,.host-unavailable{margin:0;color:#8994a7;font-size:11px;line-height:1.6}.host-unavailable{padding:9px 11px;border-radius:8px;color:#9a681f;background:#fff8e8}@media(max-width:650px){.parameter-row{grid-template-columns:1fr;gap:6px}}
</style>