<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  createScript,
  fetchScripts,
  getAutomationErrorMessage,
  importAutomationGit,
  uploadAutomationSource,
  uploadAutomationZip,
  updateScript,
} from '../services/automation'
import {
  loadHostExecutionPaths,
  rememberHostExecutionPaths,
} from '../services/execution-preferences'
import {
  createParameterEnum,
  deleteParameterEnum,
  fetchParameterEnums,
  fetchScriptParameters,
  replaceScriptParameters,
  updateParameterEnum,
} from '../services/parameters'
import {
  executePlan,
  fetchPlans,
  getTestPlanErrorMessage,
} from '../services/test-plans'
import type {
  AutomationScript,
  AutomationScriptCreateInput,
  AutomationScriptStatus,
} from '../types/automation'
import type { Engine } from '../types/platform'
import type {
  JsonScalar,
  ParameterEnumOption,
  ParameterEnumSet,
  ParameterInputType,
  ScriptParameterDefinition,
} from '../types/parameters'
import type {
  ExecutionTarget,
  PlanExecutionOptions,
  TestPlanResponse,
} from '../types/test-plan'

type SourceMode = 'keep' | 'single' | 'zip' | 'git'
type EnumOptionDraft = { label: string; valueText: string }
type ParameterDraft = {
  name: string
  label: string
  inputType: ParameterInputType
  required: boolean
  enumSetId: string
  defaultText: string
}

const props = defineProps<{
  projectId: string
  projectsLoading: boolean
  projectsError: string
}>()
const emit = defineEmits<{
  executed: [runIds: string[]]
  navigatePlans: []
}>()

const engineOptions: { value: Engine; label: string }[] = [
  { value: 'http', label: 'HTTP 接口' },
  { value: 'pytest', label: 'pytest' },
  { value: 'playwright', label: 'Playwright' },
]
const parameterTypeOptions: { value: ParameterInputType; label: string }[] = [
  { value: 'text', label: '文本' },
  { value: 'number', label: '数字' },
  { value: 'integer', label: '整数' },
  { value: 'boolean', label: '布尔值' },
  { value: 'enum', label: '枚举' },
]
const statusInfo: Record<AutomationScriptStatus, { label: string; tone: string }> = {
  DRAFT: { label: '草稿', tone: 'neutral' },
  ACTIVE: { label: '已启用', tone: 'success' },
  ARCHIVED: { label: '已归档', tone: 'muted' },
}

const scripts = ref<AutomationScript[]>([])
const loading = ref(false)
const listError = ref('')
const actionError = ref('')
const showForm = ref(false)
const editingScript = ref<AutomationScript | null>(null)
const copyingScript = ref<AutomationScript | null>(null)
const submitting = ref(false)
const formError = ref('')
const transitioningId = ref('')
const showExecution = ref(false)
const executionScript = ref<AutomationScript | null>(null)
const executionPlans = ref<TestPlanResponse[]>([])
const selectedPlanId = ref('')
const plansLoading = ref(false)
const executionError = ref('')
const executing = ref(false)
const executionTarget = ref<ExecutionTarget>('docker')
const hostProjectDirectory = ref('')
const hostPythonExecutable = ref('')
const showEnumManager = ref(false)
const enumSets = ref<ParameterEnumSet[]>([])
const enumsLoading = ref(false)
const enumListError = ref('')
const editingEnum = ref<ParameterEnumSet | null>(null)
const enumSubmitting = ref(false)
const deletingEnumId = ref('')
const enumFormError = ref('')
const enumFormSuccess = ref('')
const enumForm = ref({ name: '', description: '', options: [] as EnumOptionDraft[] })
const showParameters = ref(false)
const parameterScript = ref<AutomationScript | null>(null)
const parameterRows = ref<ParameterDraft[]>([])
const parametersLoading = ref(false)
const parametersLoadFailed = ref(false)
const parametersSubmitting = ref(false)
const parameterError = ref('')

const form = ref({
  name: '',
  description: '',
  engine: 'http' as Engine,
  entrypoint: '',
  sourceRef: '',
  contentDigest: '',
  sourceMode: 'single' as SourceMode,
  sourceContent: '',
  zipFile: null as File | null,
  gitUrl: '',
  gitRef: 'HEAD',
  timeoutSeconds: 300,
})

let listController: AbortController | null = null
let plansController: AbortController | null = null
let enumsController: AbortController | null = null
let parametersController: AbortController | null = null

const isEditing = computed(() => editingScript.value !== null)
const isCopying = computed(() => copyingScript.value !== null)
const selectedExecutionPlan = computed(() =>
  executionPlans.value.find((plan) => plan.id === selectedPlanId.value) ?? null,
)
const selectedExecutionItem = computed(() =>
  selectedExecutionPlan.value?.items.find((item) => item.script_id === executionScript.value?.id) ?? null,
)
const hostExecutionAvailable = computed(() => {
  const plan = selectedExecutionPlan.value
  return plan !== null && plan.items.every((item) => {
    const script = scripts.value.find((candidate) => candidate.id === item.script_id)
    return script?.engine === 'pytest' || script?.engine === 'playwright'
  })
})

watch(hostExecutionAvailable, (available) => {
  if (!available && executionTarget.value === 'host') executionTarget.value = 'docker'
})

async function loadScripts() {
  if (!props.projectId) {
    scripts.value = []
    return
  }
  listController?.abort()
  const currentController = new AbortController()
  listController = currentController
  loading.value = true
  listError.value = ''
  try {
    scripts.value = await fetchScripts(props.projectId, currentController.signal)
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    listError.value = getAutomationErrorMessage(reason)
  } finally {
    if (listController === currentController) loading.value = false
  }
}

watch(() => props.projectId, () => {
  actionError.value = ''
  closeExecution()
  enumsController?.abort()
  parametersController?.abort()
  showEnumManager.value = false
  showParameters.value = false
  enumSets.value = []
  void loadScripts()
}, { immediate: true })

onBeforeUnmount(() => {
  listController?.abort()
  plansController?.abort()
  enumsController?.abort()
  parametersController?.abort()
})

function resetForm() {
  form.value = {
    name: '', description: '', engine: 'http', entrypoint: '',
    sourceRef: '', contentDigest: '', sourceMode: 'single',
    sourceContent: '', zipFile: null, gitUrl: '', gitRef: 'HEAD',
    timeoutSeconds: 300,
  }
  formError.value = ''
}

function openCreateForm() {
  editingScript.value = null
  copyingScript.value = null
  resetForm()
  showForm.value = true
}

function openEditForm(script: AutomationScript) {
  editingScript.value = script
  copyingScript.value = null
  form.value = {
    name: script.name,
    description: script.description,
    engine: script.engine,
    entrypoint: script.entrypoint,
    sourceRef: script.source_ref,
    contentDigest: script.content_digest,
    sourceMode: 'keep',
    sourceContent: '',
    zipFile: null,
    gitUrl: '',
    gitRef: 'HEAD',
    timeoutSeconds: script.timeout_seconds,
  }
  formError.value = ''
  showForm.value = true
}

function copiedScriptName(name: string): string {
  const existing = new Set(scripts.value.map((script) => script.name))
  for (let number = 1; number <= scripts.value.length + 1; number += 1) {
    const suffix = number === 1 ? '（副本）' : `（副本 ${number}）`
    const candidate = `${name.slice(0, 255 - suffix.length)}${suffix}`
    if (!existing.has(candidate)) return candidate
  }
  return `${name.slice(0, 245)}（新副本）`
}

function openCopyForm(script: AutomationScript) {
  editingScript.value = null
  copyingScript.value = script
  form.value = {
    name: copiedScriptName(script.name),
    description: script.description,
    engine: script.engine,
    entrypoint: script.entrypoint,
    sourceRef: script.source_ref,
    contentDigest: script.content_digest,
    sourceMode: 'keep',
    sourceContent: '',
    zipFile: null,
    gitUrl: '',
    gitRef: 'HEAD',
    timeoutSeconds: script.timeout_seconds,
  }
  formError.value = ''
  showForm.value = true
}

function closeForm() {
  if (submitting.value) return
  showForm.value = false
  editingScript.value = null
  copyingScript.value = null
}

function selectZip(event: Event) {
  const input = event.target as HTMLInputElement
  form.value.zipFile = input.files?.[0] ?? null
}

async function submitForm() {
  const name = form.value.name.trim()
  const entrypoint = form.value.entrypoint.trim()
  formError.value = ''
  if (!name || !entrypoint) {
    formError.value = '请填写脚本名称和入口。'
    return
  }
  if (form.value.sourceMode === 'single' && form.value.sourceContent.length === 0) {
    formError.value = '请粘贴源码内容。'
    return
  }
  if (form.value.sourceMode === 'zip' && !form.value.zipFile) {
    formError.value = '请选择一个 ZIP 项目包。'
    return
  }
  if (form.value.sourceMode === 'git' && !form.value.gitUrl.trim()) {
    formError.value = '请填写公开 Git 仓库的 HTTPS 地址。'
    return
  }
  submitting.value = true
  try {
    let sourceRef = form.value.sourceRef
    let digest = form.value.contentDigest
    if (form.value.sourceMode !== 'keep') {
      let source
      if (form.value.sourceMode === 'single') {
        source = await uploadAutomationSource(props.projectId, form.value.sourceContent)
      } else if (form.value.sourceMode === 'zip') {
        source = await uploadAutomationZip(props.projectId, form.value.zipFile as File)
      } else {
        source = await importAutomationGit(
          props.projectId, form.value.gitUrl.trim(), form.value.gitRef.trim() || 'HEAD',
        )
      }
      sourceRef = source.source_ref
      digest = source.content_digest
      form.value.sourceRef = sourceRef
      form.value.contentDigest = digest
    }
    if (editingScript.value) {
      const updated = await updateScript(editingScript.value.id, {
        name,
        description: form.value.description.trim(),
        entrypoint,
        source_ref: sourceRef,
        content_digest: digest,
        timeout_seconds: form.value.timeoutSeconds,
        status: editingScript.value.status,
        state_version: editingScript.value.state_version,
      })
      scripts.value = scripts.value.map((item) => (item.id === updated.id ? updated : item))
    } else {
      const input: AutomationScriptCreateInput = {
        project_id: props.projectId,
        name,
        description: form.value.description.trim(),
        engine: form.value.engine,
        entrypoint,
        source_ref: sourceRef,
        content_digest: digest,
        timeout_seconds: form.value.timeoutSeconds,
      }
      const created = await createScript(input)
      scripts.value = [...scripts.value, created]
    }
    showForm.value = false
    editingScript.value = null
    copyingScript.value = null
  } catch (reason) {
    formError.value = getAutomationErrorMessage(reason)
  } finally {
    submitting.value = false
  }
}

async function transitionStatus(script: AutomationScript, status: AutomationScriptStatus) {
  transitioningId.value = script.id
  actionError.value = ''
  try {
    const updated = await updateScript(script.id, {
      name: script.name,
      description: script.description,
      entrypoint: script.entrypoint,
      source_ref: script.source_ref,
      content_digest: script.content_digest,
      timeout_seconds: script.timeout_seconds,
      status,
      state_version: script.state_version,
    })
    scripts.value = scripts.value.map((item) => (item.id === updated.id ? updated : item))
  } catch (reason) {
    actionError.value = getAutomationErrorMessage(reason)
  } finally {
    transitioningId.value = ''
  }
}

async function openExecution(script: AutomationScript) {
  plansController?.abort()
  const currentController = new AbortController()
  plansController = currentController
  executionScript.value = script
  executionPlans.value = []
  selectedPlanId.value = ''
  executionError.value = ''
  executionTarget.value = 'docker'
  const paths = loadHostExecutionPaths()
  hostProjectDirectory.value = paths.projectDirectory
  hostPythonExecutable.value = paths.pythonExecutable
  plansLoading.value = true
  showExecution.value = true
  try {
    const plans = await fetchPlans(props.projectId, currentController.signal)
    executionPlans.value = plans.filter((plan) =>
      plan.status === 'ACTIVE' && plan.items.some((item) => item.script_id === script.id),
    )
    selectedPlanId.value = executionPlans.value[0]?.id ?? ''
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    executionError.value = getTestPlanErrorMessage(reason)
  } finally {
    if (plansController === currentController) plansLoading.value = false
  }
}

function closeExecution() {
  if (executing.value) return
  plansController?.abort()
  showExecution.value = false
  executionScript.value = null
  executionPlans.value = []
  selectedPlanId.value = ''
  executionError.value = ''
  executionTarget.value = 'docker'
  hostProjectDirectory.value = ''
  hostPythonExecutable.value = ''
  plansLoading.value = false
}

function navigateToPlans() {
  closeExecution()
  emit('navigatePlans')
}

async function executeSelectedPlan() {
  if (!selectedExecutionPlan.value || executing.value) return
  executionError.value = ''
  let options: PlanExecutionOptions = { execution_target: 'docker' }
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
  executing.value = true
  try {
    const batch = await executePlan(selectedExecutionPlan.value.id, options)
    if (options.execution_target === 'host') rememberHostExecutionPaths(options)
    showExecution.value = false
    emit('executed', batch.run_ids)
  } catch (reason) {
    executionError.value = getTestPlanErrorMessage(reason)
  } finally {
    executing.value = false
  }
}

function parameterErrorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : '参数配置操作失败，请稍后重试。'
}

function scalarText(value: JsonScalar): string {
  return JSON.stringify(value) as string
}

function parseJsonScalar(text: string, fieldName: string): JsonScalar {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    throw new Error(`${fieldName}必须是有效的 JSON 标量，例如 "启用"、1、true 或 null。`)
  }
  if (value !== null && typeof value !== 'string' && typeof value !== 'boolean'
    && !(typeof value === 'number' && Number.isFinite(value))) {
    throw new Error(`${fieldName}仅支持字符串、有限数字、布尔值或 null。`)
  }
  return value as JsonScalar
}

function resetEnumForm() {
  editingEnum.value = null
  enumForm.value = { name: '', description: '', options: [] }
  enumFormError.value = ''
  enumFormSuccess.value = ''
}

function clearEnumFeedback() {
  enumFormError.value = ''
  enumFormSuccess.value = ''
}

async function openEnumManager() {
  enumsController?.abort()
  const currentController = new AbortController()
  enumsController = currentController
  showEnumManager.value = true
  enumsLoading.value = true
  enumListError.value = ''
  resetEnumForm()
  try {
    enumSets.value = await fetchParameterEnums(props.projectId, currentController.signal)
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    enumListError.value = parameterErrorMessage(reason)
  } finally {
    if (enumsController === currentController) enumsLoading.value = false
  }
}

function closeEnumManager() {
  if (enumSubmitting.value || deletingEnumId.value) return
  enumsController?.abort()
  showEnumManager.value = false
  resetEnumForm()
}

function editEnum(item: ParameterEnumSet) {
  editingEnum.value = item
  enumForm.value = {
    name: item.name,
    description: item.description,
    options: item.options.map((option) => ({
      label: option.label,
      valueText: scalarText(option.value),
    })),
  }
  enumFormError.value = ''
  enumFormSuccess.value = ''
}

function addEnumOption() {
  clearEnumFeedback()
  enumForm.value.options.push({ label: '', valueText: '""' })
}

function removeEnumOption(index: number) {
  clearEnumFeedback()
  enumForm.value.options.splice(index, 1)
}

function validatedEnumOptions(): ParameterEnumOption[] {
  const seen = new Set<string>()
  return enumForm.value.options.map((option, index) => {
    const label = option.label.trim()
    if (!label) throw new Error(`第 ${index + 1} 个选项的标签不能为空。`)
    const value = parseJsonScalar(option.valueText, `第 ${index + 1} 个选项值`)
    const identity = scalarText(value)
    if (seen.has(identity)) throw new Error('枚举选项值不能重复。')
    seen.add(identity)
    return { label, value }
  })
}

async function submitEnum() {
  enumFormError.value = ''
  enumFormSuccess.value = ''
  const name = enumForm.value.name.trim()
  if (!name) {
    enumFormError.value = '枚举名称不能为空。'
    return
  }
  let options: ParameterEnumOption[]
  try {
    options = validatedEnumOptions()
  } catch (reason) {
    enumFormError.value = parameterErrorMessage(reason)
    return
  }
  enumSubmitting.value = true
  try {
    const saved = editingEnum.value
      ? await updateParameterEnum(editingEnum.value.id, {
        name, description: enumForm.value.description.trim(), options,
        state_version: editingEnum.value.state_version,
      })
      : await createParameterEnum({
        project_id: props.projectId, name,
        description: enumForm.value.description.trim(), options,
      })
    enumSets.value = editingEnum.value
      ? enumSets.value.map((item) => item.id === saved.id ? saved : item)
      : [...enumSets.value, saved]
    enumListError.value = ''
    editEnum(saved)
    enumFormSuccess.value = '保存成功，枚举已更新。'
  } catch (reason) {
    enumFormError.value = parameterErrorMessage(reason)
  } finally {
    enumSubmitting.value = false
  }
}

async function removeEnumSet(item: ParameterEnumSet) {
  if (!window.confirm(`确定删除枚举“${item.name}”吗？`)) return
  deletingEnumId.value = item.id
  enumListError.value = ''
  try {
    await deleteParameterEnum(item.id)
    enumSets.value = enumSets.value.filter((candidate) => candidate.id !== item.id)
    if (editingEnum.value?.id === item.id) resetEnumForm()
  } catch (reason) {
    enumListError.value = parameterErrorMessage(reason)
  } finally {
    deletingEnumId.value = ''
  }
}

function enumOptions(enumSetId: string): ParameterEnumOption[] {
  return enumSets.value.find((item) => item.id === enumSetId)?.options ?? []
}

function definitionToDraft(item: ScriptParameterDefinition): ParameterDraft {
  let defaultText: string
  if (item.input_type === 'text') defaultText = typeof item.default_value === 'string' ? item.default_value : scalarText(item.default_value)
  else if (item.input_type === 'boolean') defaultText = item.default_value === true ? 'true' : 'false'
  else defaultText = scalarText(item.default_value)
  return {
    name: item.name, label: item.label, inputType: item.input_type,
    required: item.required, enumSetId: item.enum_set_id ?? '', defaultText,
  }
}

async function openParameters(script: AutomationScript) {
  parametersController?.abort()
  const currentController = new AbortController()
  parametersController = currentController
  parameterScript.value = script
  parameterRows.value = []
  parameterError.value = ''
  parametersLoadFailed.value = false
  parametersLoading.value = true
  showParameters.value = true
  try {
    const [definitions, availableEnums] = await Promise.all([
      fetchScriptParameters(script.id, currentController.signal),
      fetchParameterEnums(props.projectId, currentController.signal),
    ])
    enumSets.value = availableEnums
    parameterRows.value = [...definitions]
      .sort((left, right) => left.position - right.position)
      .map(definitionToDraft)
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    parametersLoadFailed.value = true
    parameterError.value = parameterErrorMessage(reason)
  } finally {
    if (parametersController === currentController) parametersLoading.value = false
  }
}

function closeParameters() {
  if (parametersSubmitting.value) return
  parametersController?.abort()
  showParameters.value = false
  parameterScript.value = null
  parameterRows.value = []
  parameterError.value = ''
  parametersLoadFailed.value = false
}

function addParameterRow() {
  parameterRows.value.push({
    name: '', label: '', inputType: 'text', required: false,
    enumSetId: '', defaultText: '',
  })
}

function removeParameterRow(index: number) {
  parameterRows.value.splice(index, 1)
}

function moveParameterRow(index: number, offset: number) {
  const target = index + offset
  if (target < 0 || target >= parameterRows.value.length) return
  const [item] = parameterRows.value.splice(index, 1)
  if (item) parameterRows.value.splice(target, 0, item)
}

function changeParameterType(row: ParameterDraft) {
  row.enumSetId = row.inputType === 'enum' ? (enumSets.value[0]?.id ?? '') : ''
  if (row.inputType === 'boolean') row.defaultText = 'false'
  else if (row.inputType === 'number' || row.inputType === 'integer') row.defaultText = '0'
  else if (row.inputType === 'enum') row.defaultText = scalarText(enumOptions(row.enumSetId)[0]?.value ?? null)
  else row.defaultText = ''
}

function changeParameterEnum(row: ParameterDraft) {
  row.defaultText = scalarText(enumOptions(row.enumSetId)[0]?.value ?? null)
}

function parseParameterDefault(row: ParameterDraft, index: number): JsonScalar {
  const field = `第 ${index + 1} 行默认值`
  if (row.inputType === 'text') return row.defaultText
  if (row.inputType === 'boolean') return row.defaultText === 'true'
  if (row.inputType === 'number' || row.inputType === 'integer') {
    if (!row.defaultText.trim()) throw new Error(`${field}不能为空。`)
    const value = Number(row.defaultText)
    if (!Number.isFinite(value)) throw new Error(`${field}必须是有限数字。`)
    if (row.inputType === 'integer' && !Number.isInteger(value)) throw new Error(`${field}必须是整数。`)
    return value
  }
  const selected = enumSets.value.find((item) => item.id === row.enumSetId)
  if (!selected) throw new Error(`第 ${index + 1} 行请选择有效的枚举。`)
  const value = parseJsonScalar(row.defaultText, field)
  if (!selected.options.some((option) => scalarText(option.value) === scalarText(value))) {
    throw new Error(`${field}必须是所选枚举中的选项。`)
  }
  return value
}

async function submitParameters() {
  if (!parameterScript.value) return
  parameterError.value = ''
  const names = new Set<string>()
  let definitions: ScriptParameterDefinition[]
  try {
    definitions = parameterRows.value.map((row, index) => {
      const name = row.name.trim()
      const label = row.label.trim()
      if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(name)) {
        throw new Error(`第 ${index + 1} 行参数键必须是字母或下划线开头的标识符。`)
      }
      if (names.has(name)) throw new Error('参数键不能重复。')
      if (!label) throw new Error(`第 ${index + 1} 行显示名称不能为空。`)
      names.add(name)
      return {
        name, label, input_type: row.inputType, required: row.required,
        enum_set_id: row.inputType === 'enum' ? row.enumSetId : null,
        default_value: parseParameterDefault(row, index), position: index,
      }
    })
  } catch (reason) {
    parameterError.value = parameterErrorMessage(reason)
    return
  }
  parametersSubmitting.value = true
  try {
    const saved = await replaceScriptParameters(parameterScript.value.id, definitions)
    parameterRows.value = saved.map(definitionToDraft)
    showParameters.value = false
    parameterScript.value = null
  } catch (reason) {
    parameterError.value = parameterErrorMessage(reason)
  } finally {
    parametersSubmitting.value = false
  }
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString()
}
</script>

<template>
  <section class="automation" aria-labelledby="automation-title">
    <header class="automation-header">
      <div>
        <h2 id="automation-title">自动化脚本</h2>
        <p>管理 HTTP / pytest / Playwright 自动化脚本，绑定测试用例并驱动执行。</p>
      </div>
      <div class="header-actions">
        <button
          type="button" class="secondary-button"
          :disabled="!projectId || projectsLoading"
          @click="openEnumManager"
        >
          枚举库
        </button>
        <button
          type="button" class="primary-button"
          :disabled="!projectId || projectsLoading"
          @click="openCreateForm"
        >
          新建脚本
        </button>
      </div>
    </header>

    <div v-if="projectsLoading" class="state-view" role="status">
      <span class="loader" aria-hidden="true"></span><strong>正在加载项目</strong>
    </div>
    <div v-else-if="projectsError" class="state-view offline-view">
      <span class="offline-icon" aria-hidden="true">!</span><strong>项目数据不可用</strong><p>{{ projectsError }}</p>
    </div>
    <div v-else-if="!projectId" class="state-view">
      <span class="empty-icon" aria-hidden="true">◇</span><strong>请先选择或新建项目</strong>
      <p>请在右上角项目下拉框中选择一个项目，或新建一个项目后再管理自动化脚本。</p>
    </div>
    <template v-else>
      <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
      <div v-if="loading" class="state-view" role="status">
        <span class="loader" aria-hidden="true"></span><strong>正在读取脚本列表</strong>
      </div>
      <div v-else-if="listError" class="state-view offline-view">
        <span class="offline-icon" aria-hidden="true">!</span><strong>脚本数据暂时不可用</strong><p>{{ listError }}</p>
        <button type="button" @click="loadScripts">重新连接</button>
      </div>
      <div v-else-if="scripts.length === 0" class="state-view">
        <span class="empty-icon" aria-hidden="true">◇</span><strong>暂无自动化脚本</strong>
        <p>点击右上角“新建脚本”创建第一个 HTTP / pytest / Playwright 脚本。</p>
      </div>
      <div v-else class="script-table" role="table" aria-label="自动化脚本列表">
        <div class="script-row script-row-head" role="row">
          <span role="columnheader">名称</span>
          <span role="columnheader">引擎</span>
          <span role="columnheader">状态</span>
          <span role="columnheader">入口</span>
          <span role="columnheader">修订</span>
          <span role="columnheader">更新时间</span>
          <span role="columnheader">操作</span>
        </div>
        <div v-for="script in scripts" :key="script.id" class="script-row" role="row">
          <span role="cell" class="script-name">
            <strong>{{ script.name }}</strong>
            <small v-if="script.description">{{ script.description }}</small>
          </span>
          <span role="cell"><span class="badge engine-badge">{{ script.engine }}</span></span>
          <span role="cell"><span class="badge" :class="`tone-${statusInfo[script.status].tone}`">{{ statusInfo[script.status].label }}</span></span>
          <span role="cell" class="script-entrypoint">{{ script.entrypoint }}</span>
          <span role="cell">v{{ script.revision }}</span>
          <span role="cell">{{ formatDate(script.updated_at) }}</span>
          <span role="cell" class="script-actions">
            <button type="button" class="link-button" @click="openParameters(script)">入参配置</button>
            <button
              v-if="script.status === 'ACTIVE'" type="button" class="execute-link"
              :disabled="transitioningId === script.id"
              @click="openExecution(script)"
            >
              执行
            </button>
            <button
              v-if="script.status !== 'ARCHIVED'" type="button" class="link-button"
              @click="openEditForm(script)"
            >
              编辑
            </button>
            <button type="button" class="link-button" @click="openCopyForm(script)">复制创建</button>
            <button
              v-if="script.status === 'DRAFT'" type="button" class="link-button"
              :disabled="transitioningId === script.id"
              @click="transitionStatus(script, 'ACTIVE')"
            >
              {{ transitioningId === script.id ? '处理中…' : '激活' }}
            </button>
            <button
              v-if="script.status === 'ACTIVE'" type="button" class="link-button danger"
              :disabled="transitioningId === script.id"
              @click="transitionStatus(script, 'ARCHIVED')"
            >
              {{ transitioningId === script.id ? '处理中…' : '归档' }}
            </button>
            <button
              v-if="script.status === 'ARCHIVED'" type="button" class="link-button"
              :disabled="transitioningId === script.id"
              @click="transitionStatus(script, 'ACTIVE')"
            >
              {{ transitioningId === script.id ? '重新启用中…' : '重新启用' }}
            </button>
          </span>
        </div>
      </div>
    </template>

    <div v-if="showForm" class="form-overlay" role="dialog" :aria-label="isEditing ? '编辑自动化脚本' : isCopying ? '复制自动化脚本' : '新建自动化脚本'">
      <div class="form-panel">
        <header>
          <h3>{{ isEditing ? '编辑自动化脚本' : isCopying ? '复制自动化脚本' : '新建自动化脚本' }}</h3>
          <button type="button" class="close-button" aria-label="关闭" :disabled="submitting" @click="closeForm">×</button>
        </header>
        <div class="form-body">
          <label class="form-field">
            <span>脚本名称</span>
            <input v-model="form.name" type="text" maxlength="255" :disabled="submitting" placeholder="例如：登录接口冒烟">
          </label>
          <label class="form-field">
            <span>描述（可选）</span>
            <input v-model="form.description" type="text" maxlength="500" :disabled="submitting" placeholder="简要描述脚本用途">
          </label>
          <label class="form-field">
            <span>执行引擎</span>
            <select v-model="form.engine" :disabled="submitting || isEditing">
              <option v-for="option in engineOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <label class="form-field">
            <span>入口（entrypoint）</span>
            <input v-model="form.entrypoint" type="text" maxlength="512" :disabled="submitting" placeholder="例如：tests/test_login.py">
          </label>
          <label class="form-field">
            <span>超时时间（秒）</span>
            <input v-model.number="form.timeoutSeconds" type="number" min="1" max="3600" :disabled="submitting">
          </label>
          <label class="form-field">
            <span>源码来源</span>
            <select v-model="form.sourceMode" :disabled="submitting">
              <option v-if="isEditing || isCopying" value="keep">{{ isCopying ? '复用原脚本源码' : '保留当前源码' }}</option>
              <option value="single">粘贴单个 Python 文件</option>
              <option value="zip">上传完整项目 ZIP</option>
              <option value="git">导入公开 Git 仓库</option>
            </select>
          </label>
          <label v-if="form.sourceMode === 'single'" class="form-field">
            <span>源码内容</span>
            <textarea v-model="form.sourceContent" rows="7" :disabled="submitting" placeholder="粘贴入口 Python 文件的完整源码"></textarea>
          </label>
          <label v-else-if="form.sourceMode === 'zip'" class="form-field">
            <span>Python 项目 ZIP</span>
            <input type="file" accept=".zip,application/zip" :disabled="submitting" @change="selectZip">
            <small>{{ form.zipFile ? `已选择：${form.zipFile.name}` : 'ZIP 根目录应直接包含入口文件及其依赖模块' }}</small>
          </label>
          <template v-else-if="form.sourceMode === 'git'">
            <label class="form-field">
              <span>公开仓库 HTTPS 地址</span>
              <input v-model="form.gitUrl" type="url" maxlength="2048" :disabled="submitting" placeholder="https://github.com/org/repository.git">
            </label>
            <label class="form-field">
              <span>分支、标签或提交</span>
              <input v-model="form.gitRef" type="text" maxlength="255" :disabled="submitting" placeholder="HEAD、main、v1.0.0 或提交 SHA">
            </label>
          </template>
          <p class="source-note">ZIP 最大 10 MiB；Git 支持 GitHub、GitLab、Bitbucket 和 Gitee 的公开 HTTPS 仓库。入口填写项目内相对路径。</p>
          <div v-if="isEditing || isCopying" class="source-metadata">
            <label class="form-field"><span>当前源码引用（只读）</span><input :value="form.sourceRef" type="text" readonly></label>
            <label class="form-field"><span>当前内容摘要（只读）</span><input :value="form.contentDigest" type="text" readonly></label>
          </div>
          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
        </div>
        <footer>
          <button type="button" class="secondary-button" :disabled="submitting" @click="closeForm">取消</button>
          <button type="button" class="primary-button" :disabled="submitting" @click="submitForm">
            {{ submitting ? '保存中…' : isEditing ? '保存修改' : isCopying ? '创建副本' : '创建脚本' }}
          </button>
        </footer>
      </div>
    </div>

    <div v-if="showEnumManager" class="form-overlay" role="dialog" aria-modal="true" aria-label="枚举库">
      <div class="form-panel manager-panel">
        <header>
          <div><h3>枚举库</h3><small>当前项目内的枚举可用于脚本入参。</small></div>
          <button type="button" class="close-button" aria-label="关闭" :disabled="enumSubmitting || Boolean(deletingEnumId)" @click="closeEnumManager">×</button>
        </header>
        <div class="enum-manager-body">
          <aside class="enum-list">
            <div class="subsection-heading">
              <strong>枚举集合</strong>
              <button type="button" class="link-button" :disabled="enumSubmitting" @click="resetEnumForm">新建</button>
            </div>
            <div v-if="enumsLoading" class="compact-loading"><span class="loader"></span>加载中…</div>
            <template v-else>
              <p v-if="enumListError" class="form-error" role="alert">{{ enumListError }}</p>
              <p v-if="enumSets.length === 0" class="empty-note">暂无枚举，填写右侧表单创建。</p>
              <div v-else class="enum-list-items">
                <div v-for="item in enumSets" :key="item.id" class="enum-list-item" :class="{ active: editingEnum?.id === item.id }">
                  <button type="button" class="enum-select" :disabled="enumSubmitting" @click="editEnum(item)">
                    <strong>{{ item.name }}</strong><small>{{ item.options.length }} 个选项</small>
                  </button>
                  <button type="button" class="icon-danger" :aria-label="`删除${item.name}`" :disabled="Boolean(deletingEnumId) || enumSubmitting" @click="removeEnumSet(item)">
                    {{ deletingEnumId === item.id ? '…' : '×' }}
                  </button>
                </div>
              </div>
            </template>
          </aside>
          <div class="enum-editor">
            <div class="subsection-heading"><strong>{{ editingEnum ? '编辑枚举' : '新建枚举' }}</strong></div>
            <label class="form-field">
              <span>名称</span>
              <input v-model="enumForm.name" type="text" maxlength="255" :disabled="enumSubmitting" placeholder="例如：运行环境" @input="clearEnumFeedback">
            </label>
            <label class="form-field">
              <span>描述（可选）</span>
              <textarea v-model="enumForm.description" rows="2" maxlength="100000" :disabled="enumSubmitting" placeholder="说明枚举用途" @input="clearEnumFeedback"></textarea>
            </label>
            <div class="subsection-heading option-heading">
              <strong>选项</strong>
              <button type="button" class="link-button" :disabled="enumSubmitting" @click="addEnumOption">添加选项</button>
            </div>
            <p class="value-hint">值使用 JSON 标量格式：字符串需加双引号，也可填写数字、true、false 或 null。</p>
            <div v-if="enumForm.options.length === 0" class="empty-note option-empty">可创建空枚举，或点击“添加选项”。</div>
            <div v-for="(option, index) in enumForm.options" :key="index" class="enum-option-row">
              <label class="form-field"><span>标签</span><input v-model="option.label" type="text" maxlength="255" :disabled="enumSubmitting" placeholder="展示名称" @input="clearEnumFeedback"></label>
              <label class="form-field"><span>JSON 值</span><input v-model="option.valueText" type="text" :disabled="enumSubmitting" placeholder='例如："staging"' @input="clearEnumFeedback"></label>
              <button type="button" class="remove-row" aria-label="移除选项" :disabled="enumSubmitting" @click="removeEnumOption(index)">移除</button>
            </div>
            <p v-if="enumFormError" class="form-error" role="alert">{{ enumFormError }}</p>
            <p v-if="enumFormSuccess" class="form-success" role="status">{{ enumFormSuccess }}</p>
          </div>
        </div>
        <footer>
          <button type="button" class="secondary-button" :disabled="enumSubmitting || Boolean(deletingEnumId)" @click="closeEnumManager">关闭</button>
          <button type="button" class="primary-button" :disabled="enumsLoading || enumSubmitting" @click="submitEnum">{{ enumSubmitting ? '保存中…' : enumFormSuccess ? '已保存' : editingEnum ? '保存修改' : '创建枚举' }}</button>
        </footer>
      </div>
    </div>

    <div v-if="showParameters" class="form-overlay" role="dialog" aria-modal="true" aria-label="脚本入参配置">
      <div class="form-panel parameter-panel">
        <header>
          <div><h3>“{{ parameterScript?.name }}”入参配置</h3><small>参数按当前顺序传递，保存后供测试计划配置默认值。</small></div>
          <button type="button" class="close-button" aria-label="关闭" :disabled="parametersSubmitting" @click="closeParameters">×</button>
        </header>
        <div class="form-body parameter-body">
          <div v-if="parametersLoading" class="compact-loading"><span class="loader"></span>正在读取入参和枚举…</div>
          <template v-else-if="!parametersLoadFailed">
            <div class="parameter-toolbar">
              <span>共 {{ parameterRows.length }} 个参数</span>
              <button type="button" class="secondary-button" :disabled="parametersSubmitting" @click="addParameterRow">添加参数</button>
            </div>
            <p v-if="parameterRows.length === 0 && !parameterError" class="empty-note parameter-empty">暂无入参，点击“添加参数”开始配置。</p>
            <div v-for="(row, index) in parameterRows" :key="index" class="parameter-row">
              <div class="parameter-row-title">
                <strong>参数 {{ index + 1 }}</strong>
                <div class="row-actions">
                  <button type="button" :disabled="index === 0 || parametersSubmitting" @click="moveParameterRow(index, -1)">上移</button>
                  <button type="button" :disabled="index === parameterRows.length - 1 || parametersSubmitting" @click="moveParameterRow(index, 1)">下移</button>
                  <button type="button" class="danger" :disabled="parametersSubmitting" @click="removeParameterRow(index)">移除</button>
                </div>
              </div>
              <div class="parameter-grid">
                <label class="form-field"><span>参数键</span><input v-model="row.name" type="text" maxlength="128" :disabled="parametersSubmitting" placeholder="例如：environment"></label>
                <label class="form-field"><span>显示名称</span><input v-model="row.label" type="text" maxlength="255" :disabled="parametersSubmitting" placeholder="例如：运行环境"></label>
                <label class="form-field"><span>输入类型</span><select v-model="row.inputType" :disabled="parametersSubmitting" @change="changeParameterType(row)"><option v-for="option in parameterTypeOptions" :key="option.value" :value="option.value">{{ option.label }}（{{ option.value }}）</option></select></label>
                <label class="required-field"><input v-model="row.required" type="checkbox" :disabled="parametersSubmitting"><span>必填</span></label>
                <label v-if="row.inputType === 'enum'" class="form-field">
                  <span>枚举集合</span>
                  <select v-model="row.enumSetId" :disabled="parametersSubmitting" @change="changeParameterEnum(row)">
                    <option value="" disabled>请选择枚举</option>
                    <option v-for="item in enumSets" :key="item.id" :value="item.id">{{ item.name }}</option>
                  </select>
                </label>
                <label class="form-field default-field">
                  <span>默认值</span>
                  <select v-if="row.inputType === 'enum'" v-model="row.defaultText" :disabled="parametersSubmitting || enumOptions(row.enumSetId).length === 0">
                    <option v-for="option in enumOptions(row.enumSetId)" :key="scalarText(option.value)" :value="scalarText(option.value)">{{ option.label }}（{{ scalarText(option.value) }}）</option>
                  </select>
                  <select v-else-if="row.inputType === 'boolean'" v-model="row.defaultText" :disabled="parametersSubmitting"><option value="true">true</option><option value="false">false</option></select>
                  <input v-else v-model="row.defaultText" :type="row.inputType === 'text' ? 'text' : 'number'" :step="row.inputType === 'integer' ? '1' : 'any'" :disabled="parametersSubmitting" :placeholder="row.inputType === 'text' ? '默认文本' : '默认数字'">
                </label>
              </div>
            </div>
          </template>
          <p v-if="parameterError" class="form-error" role="alert">{{ parameterError }}</p>
        </div>
        <footer>
          <button type="button" class="secondary-button" :disabled="parametersSubmitting" @click="closeParameters">取消</button>
          <button type="button" class="primary-button" :disabled="parametersLoading || parametersLoadFailed || parametersSubmitting" @click="submitParameters">{{ parametersSubmitting ? '保存中…' : '保存入参' }}</button>
        </footer>
      </div>
    </div>

    <div v-if="showExecution" class="form-overlay" role="dialog" aria-modal="true" aria-label="执行自动化脚本">
      <div class="form-panel execution-panel">
        <header>
          <div><h3>执行“{{ executionScript?.name }}”</h3><small>通过已启用测试计划执行，确保使用已保存的入参和环境。</small></div>
          <button type="button" class="close-button" aria-label="关闭" :disabled="executing" @click="closeExecution">×</button>
        </header>
        <div class="form-body">
          <div v-if="plansLoading" class="execution-loading" role="status"><span class="loader"></span>正在读取关联计划…</div>
          <template v-else-if="executionPlans.length">
            <label class="form-field">
              <span>选择已启用计划</span>
              <select v-model="selectedPlanId" :disabled="executing">
                <option v-for="plan in executionPlans" :key="plan.id" :value="plan.id">{{ plan.name }}（v{{ plan.revision }}）</option>
              </select>
            </label>
            <label class="form-field">
              <span>本批次执行位置</span>
              <select v-model="executionTarget" :disabled="executing">
                <option value="docker">Docker 隔离环境</option>
                <option value="host" :disabled="!hostExecutionAvailable">Windows 本机执行</option>
              </select>
            </label>
            <p class="execution-warning">本次选择仅适用于此批次，不会修改脚本或测试计划配置。</p>
            <p v-if="!hostExecutionAvailable" class="host-unavailable">Windows 本机执行不可用：计划中的所有脚本必须均为 pytest 或 Playwright。</p>
            <template v-if="executionTarget === 'host'">
              <label class="form-field">
                <span>Windows 项目目录</span>
                <input v-model="hostProjectDirectory" type="text" maxlength="2048" :disabled="executing" placeholder="D:\automation\my-project">
              </label>
              <label class="form-field">
                <span>Python 解释器</span>
                <input v-model="hostPythonExecutable" type="text" maxlength="2048" :disabled="executing" placeholder="D:\automation\my-project\.venv\Scripts\python.exe">
              </label>
            </template>
            <div v-if="selectedExecutionPlan" class="execution-summary">
              <p><strong>本次将创建 {{ selectedExecutionPlan.items.length }} 个脚本任务</strong></p>
              <p v-if="selectedExecutionPlan.items.length > 1">该计划还包含其他脚本，确认后会一起执行。</p>
              <p v-if="selectedExecutionItem">当前脚本使用修订 v{{ selectedExecutionItem.script_revision }}，执行入参：</p>
              <pre>{{ JSON.stringify(selectedExecutionItem?.default_parameters ?? {}, null, 2) }}</pre>
            </div>
          </template>
          <div v-else-if="!executionError" class="no-plan-notice">
            <strong>此脚本还没有关联的已启用测试计划</strong>
            <p>请先创建测试计划、配置执行入参并启用，然后再执行。</p>
          </div>
          <p v-if="executionError" class="form-error" role="alert">{{ executionError }}</p>
        </div>
        <footer>
          <button v-if="!plansLoading && executionPlans.length === 0" type="button" class="primary-button" @click="navigateToPlans">前往测试计划</button>
          <template v-else>
            <button type="button" class="secondary-button" :disabled="executing" @click="closeExecution">取消</button>
            <button type="button" class="primary-button" :disabled="plansLoading || !selectedExecutionPlan || executing" @click="executeSelectedPlan">{{ executing ? '创建执行批次中…' : '确认执行' }}</button>
          </template>
        </footer>
      </div>
    </div>
  </section>
</template>

<style scoped>
.automation{display:flex;flex-direction:column;gap:18px}
.automation-header{display:flex;align-items:center;justify-content:space-between;gap:16px}
.automation-header h2{margin:0;color:#1c273a;font-size:20px}
.automation-header p{margin:6px 0 0;color:#8994a7;font-size:12px;line-height:1.6}
.header-actions{display:flex;align-items:center;gap:10px;flex-shrink:0}
.primary-button{height:40px;padding:0 18px;border:0;border-radius:9px;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc);font-size:13px;font-weight:700;cursor:pointer}
.primary-button:disabled{cursor:not-allowed;opacity:.6}
.secondary-button{height:36px;padding:0 14px;border:1px solid #e2e7ef;border-radius:8px;color:#556176;background:#fff;font-size:12px;font-weight:600;cursor:pointer}
.secondary-button:disabled{cursor:wait;opacity:.65}
.state-view{min-height:220px;border:1px solid #e7ecf3;border-radius:14px;background:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:6px;padding:24px}
.state-view strong{color:#28354a;font-size:15px}
.state-view p{max-width:420px;margin:0;color:#8d98aa;font-size:12px;line-height:1.7}
.offline-view{border-color:#ffd7db}
.action-error{margin:0;padding:10px 12px;border:1px solid #ffd7db;border-radius:8px;color:#b84350;background:#fff5f6;font-size:12px}
.offline-icon{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:#fff;background:#e06070;font-weight:800}
.empty-icon{font-size:22px;color:#5d80dc}
.loader{width:20px;height:20px;border:3px solid #dbe4f5;border-top-color:#4f72ca;border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.script-table{border:1px solid #e7ecf3;border-radius:14px;background:#fff;overflow:hidden}
.script-row{display:grid;grid-template-columns:2fr .8fr .8fr 1.6fr .6fr 1.2fr 2.2fr;gap:10px;padding:14px 18px;align-items:center;border-bottom:1px solid #f0f3f8}
.script-row:last-child{border-bottom:0}
.script-row-head{background:#f8fafc;color:#8994a7;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.script-name{display:flex;flex-direction:column;gap:2px;overflow:hidden}
.script-name strong{color:#1c273a;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.script-name small{color:#8994a7;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.script-entrypoint{color:#556176;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{display:inline-flex;align-items:center;padding:3px 9px;border-radius:6px;font-size:11px;font-weight:700}
.engine-badge{color:#4f72ca;background:#eaf0ff;text-transform:uppercase}
.tone-neutral{color:#70809a;background:#f1f4f8}
.tone-success{color:#1c8a5a;background:#e5f7ee}
.tone-muted{color:#8994a7;background:#f1f4f8}
.script-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.execute-link{height:28px;padding:0 12px;border:0;border-radius:7px;color:#fff;background:#526fda;font-size:12px;font-weight:700;cursor:pointer}
.execute-link:disabled{cursor:not-allowed;opacity:.6}
.link-button{border:0;background:none;padding:0;color:#4f72ca;font-size:12px;font-weight:700;cursor:pointer}
.link-button:disabled{cursor:wait;opacity:.6}
.link-button.danger{color:#c04654}
.form-overlay{position:fixed;inset:0;background:rgba(20,28,45,.4);display:flex;align-items:center;justify-content:center;z-index:50;padding:20px}
.form-panel{width:min(560px,100%);max-height:90vh;overflow-y:auto;border-radius:16px;background:#fff;box-shadow:0 24px 60px rgba(20,28,45,.3)}
.form-panel header{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid #eef1f6}
.form-panel h3{margin:0;color:#1c273a;font-size:16px}
.close-button{width:30px;height:30px;border:0;border-radius:8px;background:#f1f4f8;color:#556176;font-size:16px;cursor:pointer}
.form-body{padding:20px 24px;display:grid;gap:14px}
.form-field{display:grid;gap:6px;font-size:12px;color:#556176}
.form-field input,.form-field select,.form-field textarea{padding:0 12px;border:1px solid #dfe5ee;border-radius:8px;color:#253044;background:#fbfcfe;font:500 13px inherit}
.form-field input,.form-field select{height:40px}
.form-field textarea{padding-top:10px;resize:vertical;font-family:inherit}
.form-field input:focus,.form-field select:focus,.form-field textarea:focus{border-color:#6b8de2;outline:0}
.source-note{margin:-4px 0 0;color:#667cae;font-size:11px;line-height:1.5}
.source-metadata{display:grid;gap:10px;padding:12px;border-radius:9px;background:#f6f8fc}
.source-metadata input{color:#7c8799;background:#eef2f7}
.form-error{margin:0;padding:10px 12px;border:1px solid #ffd7db;border-radius:8px;color:#b84350;background:#fff5f6;font-size:12px;line-height:1.5}
.form-success{margin:0;padding:10px 12px;border:1px solid #ccebd8;border-radius:8px;color:#287a49;background:#f2fbf5;font-size:12px;line-height:1.5}
.form-panel footer{display:flex;justify-content:flex-end;gap:10px;padding:16px 24px;border-top:1px solid #eef1f6}
.manager-panel,.parameter-panel{width:min(980px,100%)}
.manager-panel>header>div,.parameter-panel>header>div{display:grid;gap:5px}
.manager-panel>header small,.parameter-panel>header small{color:#8994a7;font-size:11px}
.enum-manager-body{display:grid;grid-template-columns:250px minmax(0,1fr);min-height:420px;max-height:calc(90vh - 140px);overflow:hidden}
.enum-list{padding:18px;border-right:1px solid #eef1f6;background:#f8fafc;overflow-y:auto}
.enum-editor{display:grid;align-content:start;gap:13px;padding:18px 22px;overflow-y:auto}
.subsection-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;color:#344158;font-size:12px}
.enum-list-items{display:grid;gap:7px;margin-top:12px}
.enum-list-item{display:flex;align-items:center;border:1px solid #e2e7ef;border-radius:9px;background:#fff;overflow:hidden}
.enum-list-item.active{border-color:#7897e4;box-shadow:0 0 0 2px #e8efff}
.enum-select{min-width:0;flex:1;display:grid;gap:3px;padding:10px 11px;border:0;text-align:left;background:transparent;cursor:pointer}
.enum-select strong{overflow:hidden;color:#344158;font-size:12px;text-overflow:ellipsis;white-space:nowrap}
.enum-select small{color:#8994a7;font-size:10px}
.icon-danger{width:30px;height:30px;margin-right:5px;border:0;border-radius:7px;color:#c04654;background:transparent;font-size:16px;cursor:pointer}
.icon-danger:hover{background:#fff0f2}.icon-danger:disabled{cursor:wait;opacity:.5}
.compact-loading{min-height:100px;display:flex;align-items:center;justify-content:center;gap:9px;color:#66748a;font-size:12px}
.compact-loading .loader{width:16px;height:16px;border-width:2px}
.empty-note{margin:14px 0 0;padding:14px;border:1px dashed #dfe5ee;border-radius:9px;color:#8994a7;text-align:center;font-size:11px;line-height:1.6}
.option-heading{margin-top:3px}.option-empty{margin:0}.value-hint{margin:-7px 0 0;color:#667cae;font-size:11px}
.enum-option-row{display:grid;grid-template-columns:1fr 1fr auto;align-items:end;gap:9px;padding:11px;border:1px solid #e8edf4;border-radius:10px;background:#fbfcfe}
.remove-row{height:40px;padding:0 10px;border:0;color:#c04654;background:transparent;font-size:11px;font-weight:700;cursor:pointer}
.remove-row:disabled{cursor:wait;opacity:.5}
.parameter-body{display:block;max-height:calc(90vh - 140px);overflow-y:auto}
.parameter-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;color:#66748a;font-size:12px}
.parameter-empty{margin:0}.parameter-row{display:grid;gap:12px;margin-bottom:12px;padding:14px;border:1px solid #e5eaf2;border-radius:11px;background:#fbfcfe}
.parameter-row-title{display:flex;align-items:center;justify-content:space-between;color:#344158;font-size:12px}
.row-actions{display:flex;gap:5px}.row-actions button{padding:3px 7px;border:0;border-radius:5px;color:#5572bd;background:#edf2fc;font-size:10px;cursor:pointer}
.row-actions button.danger{color:#b84350;background:#fff0f2}.row-actions button:disabled{cursor:not-allowed;opacity:.45}
.parameter-grid{display:grid;grid-template-columns:1fr 1fr 1fr auto;align-items:end;gap:10px}
.required-field{height:40px;display:flex;align-items:center;gap:6px;padding:0 9px;color:#556176;font-size:12px;white-space:nowrap}
.required-field input{width:15px;height:15px;accent-color:#5578d6}
.default-field{grid-column:span 2}
@media (max-width:760px){.enum-manager-body{grid-template-columns:1fr;overflow-y:auto}.enum-list{max-height:220px;border-right:0;border-bottom:1px solid #eef1f6}.enum-editor{overflow:visible}.enum-option-row,.parameter-grid{grid-template-columns:1fr}.default-field{grid-column:auto}.parameter-body{overflow-y:auto}}
.execution-panel>header>div{display:grid;gap:5px}.execution-panel>header small{color:#8994a7;font-size:11px}.execution-loading{min-height:100px;display:flex;align-items:center;justify-content:center;gap:9px;color:#66748a;font-size:12px}.execution-summary{padding:14px;border:1px solid #dbe5fa;border-radius:10px;background:#f8faff}.execution-summary p{margin:0 0 7px;color:#66748a;font-size:12px}.execution-summary strong{color:#344e92}.execution-summary pre{max-height:180px;overflow:auto;margin:8px 0 0;padding:10px;border-radius:7px;color:#344158;background:#fff;font:11px/1.5 Consolas,monospace;white-space:pre-wrap}.no-plan-notice{padding:18px;border:1px solid #f1dba3;border-radius:10px;text-align:center;background:#fffaf0}.no-plan-notice strong{color:#7e6119;font-size:13px}.no-plan-notice p{margin:7px 0 0;color:#8a7a55;font-size:11px;line-height:1.6}
.execution-warning,.host-unavailable{margin:-4px 0 0;font-size:11px;line-height:1.5}.execution-warning{color:#667cae}.host-unavailable{color:#9a6b24}
</style>
