<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import {
  cancelLoadTestRun, createLoadTest, deleteLoadTest, fetchLoadTestRuns, fetchLoadTests, getLoadTest,
  getLoadTestRun, getLoadTestingErrorMessage, startLoadTestRun, updateLoadTest,
} from '../services/load-testing'
import { fetchEnvironments, getEnvironmentErrorMessage } from '../services/environments'
import type { Environment } from '../types/environment'
import type {
  HttpMethod, JsonObject, JsonValue, LoadTestCreateInput, LoadTestDefinition,
  LoadTestMode, LoadTestRun, LoadTestTarget, LoadTestTrafficMode,
} from '../types/load-testing'

const props = defineProps<{ projectId: string; projectsLoading: boolean; projectsError: string }>()
const methods: HttpMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const statusLabels = { RUNNING: '运行中', SUCCEEDED: '已完成', FAILED: '失败' } as const

interface TargetDraft {
  key: string
  name: string
  method: HttpMethod
  url: string
  headers: string
  query: string
  body: string
  expectedStatuses: string
  extractors: string
  thinkTimeMs: number
}

interface DefinitionDraft {
  id: string
  environmentId: string
  name: string
  description: string
  targets: TargetDraft[]
  trafficMode: LoadTestTrafficMode
  initialVariables: string
  stopOnFailure: boolean
  mode: LoadTestMode
  requestCount: number
  durationSeconds: number
  concurrency: number
  intervalMs: number
  timeoutSeconds: number
  stateVersion: number
}

interface TargetMetricRow {
  name: string
  total: number | null
  successful: number | null
  failed: number | null
  rps: number | null
  average: number | null
  p95: number | null
}

const definitions = shallowRef<LoadTestDefinition[]>([])
const environments = shallowRef<Environment[]>([])
const draft = ref<DefinitionDraft | null>(null)
const runs = ref<LoadTestRun[]>([])
const selectedRun = ref<LoadTestRun | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const runsLoading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const starting = ref(false)
const cancelling = ref(false)
const dirty = ref(false)
const error = ref('')
const success = ref('')
const environmentsLoading = ref(false)
const environmentsError = ref('')
let listController: AbortController | null = null
let environmentsController: AbortController | null = null
let detailController: AbortController | null = null
let runsController: AbortController | null = null
let pollController: AbortController | null = null
let pollTimer: number | null = null
let disposed = false

const metrics = computed(() => selectedRun.value?.metrics ?? null)
const successRate = computed(() => {
  const value = metrics.value
  return value && value.total_requests > 0
    ? `${(value.successful_requests / value.total_requests * 100).toFixed(1)}%`
    : '—'
})
const scenarioSuccessRate = computed(() => {
  const value = metrics.value?.scenario_metrics
  return value && value.total_scenarios > 0
    ? `${(value.successful_scenarios / value.total_scenarios * 100).toFixed(1)}%`
    : '—'
})

function isRecord(value: JsonValue): value is { [key: string]: JsonValue } {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const targetMetricRows = computed<TargetMetricRow[]>(() => {
  const value = metrics.value?.target_metrics
  if (!value) return []
  return Object.entries(value).map(([name, item]) => ({
    name, total: item.total_requests, successful: item.successful_requests,
    failed: item.failed_requests, rps: item.requests_per_second,
    average: item.average_ms, p95: item.p95_ms,
  }))
})

function blankTarget(index: number): TargetDraft {
  return { key: crypto.randomUUID(), name: `接口 ${index}`, method: 'GET', url: '',
    headers: '{}', query: '{}', body: 'null', expectedStatuses: '200',
    extractors: '{}', thinkTimeMs: 0 }
}

function fromDefinition(item: LoadTestDefinition): DefinitionDraft {
  return { id: item.id, environmentId: item.environment_id ?? '', name: item.name, description: item.description,
    targets: item.targets.map((target) => ({ key: crypto.randomUUID(), name: target.name,
      method: target.method, url: target.url, headers: JSON.stringify(target.headers, null, 2),
      query: JSON.stringify(target.query, null, 2), body: JSON.stringify(target.body, null, 2),
      expectedStatuses: target.expected_statuses.join(', '),
      extractors: JSON.stringify(target.extractors, null, 2), thinkTimeMs: target.think_time_ms })),
    trafficMode: item.traffic_mode, initialVariables: JSON.stringify(item.initial_variables, null, 2),
    stopOnFailure: item.stop_on_failure,
    mode: item.mode, requestCount: item.request_count, durationSeconds: item.duration_seconds,
    concurrency: item.concurrency, intervalMs: item.interval_ms,
    timeoutSeconds: item.timeout_seconds, stateVersion: item.state_version }
}

function newDraft(source?: DefinitionDraft): DefinitionDraft {
  if (!source) return { id: '', environmentId: '', name: '', description: '', targets: [blankTarget(1)],
    trafficMode: 'REQUESTS', initialVariables: '{}', stopOnFailure: true, mode: 'COUNT',
    requestCount: 1, durationSeconds: 60, concurrency: 1, intervalMs: 0,
    timeoutSeconds: 30, stateVersion: 0 }
  return { id: '', environmentId: source.environmentId, name: `${source.name} 副本`,
    description: source.description, trafficMode: source.trafficMode,
    initialVariables: source.initialVariables, stopOnFailure: source.stopOnFailure,
    mode: source.mode, requestCount: source.requestCount, durationSeconds: source.durationSeconds,
    concurrency: source.concurrency, intervalMs: source.intervalMs,
    timeoutSeconds: source.timeoutSeconds, stateVersion: 0,
    targets: source.targets.map((target) => ({ key: crypto.randomUUID(), name: target.name,
      method: target.method, url: target.url, headers: target.headers, query: target.query,
      body: target.body, expectedStatuses: target.expectedStatuses,
      extractors: target.extractors, thinkTimeMs: target.thinkTimeMs })) }
}

function setFeedback(message = '') {
  error.value = message
  success.value = ''
}

function markDirty() {
  dirty.value = true
  success.value = ''
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

function formatNumber(value: number | null, digits = 1): string {
  return value === null ? '—' : value.toFixed(digits)
}

function parseJson(text: string, label: string): JsonValue {
  try {
    const value: unknown = JSON.parse(text)
    if (!isJson(value)) throw new Error()
    return value
  } catch {
    throw new Error(`${label}必须是有效的 JSON。`)
  }
}

function isJson(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isJson)
  return typeof value === 'object' && Object.values(value as Record<string, unknown>).every(isJson)
}

function parseObject(text: string, label: string): JsonObject {
  const value = parseJson(text, label)
  if (!isRecord(value)) throw new Error(`${label}必须是 JSON 对象。`)
  return value
}

function parseStringMap(text: string, label: string): Record<string, string> {
  const value = parseObject(text, label)
  if (Object.values(value).some((item) => typeof item !== 'string')) {
    throw new Error(`${label}的属性值必须是字符串。`)
  }
  return value as Record<string, string>
}

function boundedInteger(value: number, label: string, minimum: number, maximum: number) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label}必须是 ${minimum} 到 ${maximum} 之间的整数。`)
  }
}

function parseTarget(target: TargetDraft, index: number, allowRelative: boolean,
  trafficMode: LoadTestTrafficMode): LoadTestTarget {
  const label = `目标 ${index + 1}`
  if (!target.name.trim()) throw new Error(`${label}的名称不能为空。`)
  if (!target.url.trim()) throw new Error(`${label}的 URL 不能为空。`)
  const rawUrl = target.url.trim()
  const relative = rawUrl.startsWith('/') && !rawUrl.startsWith('//')
  if (!(allowRelative && relative)) {
    try {
      const url = new URL(rawUrl)
      if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error()
    } catch {
      throw new Error(allowRelative
        ? `${label}的 URL 必须是有效的 HTTP(S) 地址或以 / 开头的相对路径。`
        : `${label}的 URL 必须是有效的 HTTP 或 HTTPS 地址。`)
    }
  }
  const statuses = [...new Set(target.expectedStatuses.split(/[\s,，]+/).filter(Boolean).map(Number))]
  if (!statuses.length || statuses.some((status) => !Number.isInteger(status) || status < 100 || status > 599)) {
    throw new Error(`${label}的期望状态码必须是 100 到 599 之间的整数。`)
  }
  boundedInteger(target.thinkTimeMs, `${label}步骤等待`, 0, 60000)
  return { name: target.name.trim(), method: target.method, url: rawUrl,
    headers: parseObject(target.headers, `${label}请求头`), query: parseObject(target.query, `${label}查询参数`),
    body: parseJson(target.body, `${label}请求体`), expected_statuses: statuses,
    extractors: trafficMode === 'SCENARIO' ? parseStringMap(target.extractors, `${label}响应提取`) : {},
    think_time_ms: trafficMode === 'SCENARIO' ? target.thinkTimeMs : 0 }
}

function buildInput(): LoadTestCreateInput {
  const value = draft.value
  if (!value) throw new Error('请先选择或新建压测配置。')
  if (!value.name.trim()) throw new Error('压测名称不能为空。')
  if (value.targets.length < 1 || value.targets.length > 20) throw new Error('接口目标数量必须为 1 到 20 个。')
  boundedInteger(value.requestCount, value.trafficMode === 'SCENARIO' ? '场景次数' : '请求次数', 1, 10000)
  boundedInteger(value.durationSeconds, '持续时间', 1, 1800)
  boundedInteger(value.concurrency, value.trafficMode === 'SCENARIO' ? '虚拟用户数' : '并发数', 1, 100)
  boundedInteger(value.intervalMs, value.trafficMode === 'SCENARIO' ? '场景间隔' : '请求间隔', 0, 60000)
  if (!Number.isFinite(value.timeoutSeconds) || value.timeoutSeconds <= 0 || value.timeoutSeconds > 120) {
    throw new Error('超时时间必须大于 0 且不超过 120 秒。')
  }
  return { project_id: props.projectId, environment_id: value.environmentId || null,
    name: value.name.trim(), description: value.description.trim(),
    targets: value.targets.map((target, index) => parseTarget(
      target, index, Boolean(value.environmentId), value.trafficMode,
    )),
    traffic_mode: value.trafficMode, initial_variables: parseObject(value.initialVariables, '初始变量'),
    stop_on_failure: value.stopOnFailure,
    mode: value.mode, request_count: value.requestCount,
    duration_seconds: value.durationSeconds, concurrency: value.concurrency,
    interval_ms: value.intervalMs, timeout_seconds: value.timeoutSeconds }
}

function stopPolling() {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
  pollController?.abort()
  pollController = null
}

function schedulePolling() {
  if (disposed || !runs.value.some((run) => run.status === 'RUNNING')) return
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(() => void pollRunningRuns(), 1500)
}

async function pollRunningRuns() {
  const ids = runs.value.filter((run) => run.status === 'RUNNING').map((run) => run.id)
  if (!ids.length) return
  const controller = new AbortController()
  pollController = controller
  try {
    const updates = await Promise.all(ids.map((id) => getLoadTestRun(id, controller.signal)))
    for (const update of updates) {
      const index = runs.value.findIndex((run) => run.id === update.id)
      if (index >= 0) runs.value[index] = update
      if (selectedRun.value?.id === update.id) selectedRun.value = update
    }
  } catch (reason) {
    if (!(reason instanceof Error && reason.name === 'AbortError')) setFeedback(getLoadTestingErrorMessage(reason))
  } finally {
    if (pollController === controller) pollController = null
    schedulePolling()
  }
}

async function loadRuns(id: string) {
  stopPolling()
  runsController?.abort()
  const controller = new AbortController()
  runsController = controller
  runsLoading.value = true
  try {
    runs.value = await fetchLoadTestRuns(id, controller.signal)
    selectedRun.value = runs.value.find((run) => run.id === selectedRun.value?.id) ?? runs.value[0] ?? null
    schedulePolling()
  } catch (reason) {
    if (!(reason instanceof Error && reason.name === 'AbortError')) setFeedback(getLoadTestingErrorMessage(reason))
  } finally {
    if (runsController === controller) runsLoading.value = false
  }
}

async function chooseDefinition(item: LoadTestDefinition) {
  detailController?.abort()
  draft.value = fromDefinition(item)
  dirty.value = false
  setFeedback()
  runs.value = []
  selectedRun.value = null
  void loadRuns(item.id)
  const controller = new AbortController()
  detailController = controller
  detailLoading.value = true
  try {
    const detail = await getLoadTest(item.id, controller.signal)
    if (draft.value?.id === detail.id && !dirty.value) draft.value = fromDefinition(detail)
  } catch (reason) {
    if (!(reason instanceof Error && reason.name === 'AbortError')) setFeedback(getLoadTestingErrorMessage(reason))
  } finally {
    if (detailController === controller) detailLoading.value = false
  }
}

async function loadDefinitions() {
  if (!props.projectId) { definitions.value = []; draft.value = null; return }
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  loading.value = true
  setFeedback()
  try {
    definitions.value = await fetchLoadTests(props.projectId, controller.signal)
    const selected = definitions.value.find((item) => item.id === draft.value?.id) ?? definitions.value[0]
    if (selected) await chooseDefinition(selected)
    else { draft.value = null; runs.value = []; selectedRun.value = null }
  } catch (reason) {
    if (!(reason instanceof Error && reason.name === 'AbortError')) setFeedback(getLoadTestingErrorMessage(reason))
  } finally {
    if (listController === controller) loading.value = false
  }
}

async function loadActiveEnvironments() {
  if (!props.projectId) { environments.value = []; return }
  environmentsController?.abort()
  const controller = new AbortController()
  environmentsController = controller
  environmentsLoading.value = true
  environmentsError.value = ''
  try {
    const items = await fetchEnvironments(props.projectId, controller.signal)
    environments.value = items.filter((item) => item.status === 'ACTIVE')
  } catch (reason) {
    if (!(reason instanceof Error && reason.name === 'AbortError')) {
      environments.value = []
      environmentsError.value = getEnvironmentErrorMessage(reason)
    }
  } finally {
    if (environmentsController === controller) environmentsLoading.value = false
  }
}

function createDraft() {
  stopPolling()
  draft.value = newDraft()
  runs.value = []
  selectedRun.value = null
  dirty.value = true
  setFeedback()
}

function copyDraft() {
  if (!draft.value) return
  stopPolling()
  draft.value = newDraft(draft.value)
  runs.value = []
  selectedRun.value = null
  dirty.value = true
  setFeedback()
}

function addTarget() {
  if (!draft.value || draft.value.targets.length >= 20) return
  draft.value.targets.push(blankTarget(draft.value.targets.length + 1))
  markDirty()
}

function removeTarget(index: number) {
  if (!draft.value || draft.value.targets.length <= 1) return
  draft.value.targets.splice(index, 1)
  markDirty()
}

function moveTarget(index: number, offset: -1 | 1) {
  if (!draft.value) return
  const destination = index + offset
  if (destination < 0 || destination >= draft.value.targets.length) return
  const targets = draft.value.targets
  const [target] = targets.splice(index, 1)
  if (!target) return
  targets.splice(destination, 0, target)
  markDirty()
}

async function persist(showMessage = true): Promise<LoadTestDefinition | null> {
  const currentDraft = draft.value
  if (!currentDraft || saving.value) return null
  let input: LoadTestCreateInput
  try { input = buildInput() } catch (reason) { setFeedback(getLoadTestingErrorMessage(reason)); return null }
  saving.value = true
  setFeedback()
  try {
    const updateInput = { environment_id: input.environment_id, name: input.name,
      description: input.description, targets: input.targets,
      traffic_mode: input.traffic_mode, initial_variables: input.initial_variables,
      stop_on_failure: input.stop_on_failure,
      mode: input.mode, request_count: input.request_count, duration_seconds: input.duration_seconds,
      concurrency: input.concurrency, interval_ms: input.interval_ms,
      timeout_seconds: input.timeout_seconds, state_version: currentDraft.stateVersion }
    const saved = currentDraft.id
      ? await updateLoadTest(currentDraft.id, updateInput)
      : await createLoadTest(input)
    const index = definitions.value.findIndex((item) => item.id === saved.id)
    definitions.value = index >= 0
      ? definitions.value.map((item) => item.id === saved.id ? saved : item)
      : [...definitions.value, saved]
    draft.value = fromDefinition(saved)
    dirty.value = false
    if (showMessage) success.value = '压测配置已保存。'
    return saved
  } catch (reason) { setFeedback(getLoadTestingErrorMessage(reason)); return null }
  finally { saving.value = false }
}

async function removeDefinition() {
  const id = draft.value?.id
  if (!id || deleting.value || !window.confirm(`确认删除“${draft.value?.name}”吗？`)) return
  deleting.value = true
  setFeedback()
  try {
    await deleteLoadTest(id)
    definitions.value = definitions.value.filter((item) => item.id !== id)
    stopPolling()
    const next = definitions.value[0]
    if (next) await chooseDefinition(next)
    else { draft.value = null; runs.value = []; selectedRun.value = null }
    success.value = '压测配置已删除。'
  } catch (reason) { setFeedback(getLoadTestingErrorMessage(reason)) }
  finally { deleting.value = false }
}

async function startRun() {
  if (!draft.value || starting.value) return
  const saved = dirty.value || !draft.value.id ? await persist(false) : definitions.value.find((item) => item.id === draft.value?.id)
  if (!saved) return
  starting.value = true
  setFeedback()
  try {
    const run = await startLoadTestRun(saved.id)
    runs.value = [run, ...runs.value.filter((item) => item.id !== run.id)]
    selectedRun.value = run
    success.value = '压测任务已启动，结果将自动刷新。'
    schedulePolling()
  } catch (reason) { setFeedback(getLoadTestingErrorMessage(reason)) }
  finally { starting.value = false }
}

async function cancelRun() {
  const run = selectedRun.value
  if (!run || run.status !== 'RUNNING' || cancelling.value) return
  cancelling.value = true
  setFeedback()
  try {
    const updated = await cancelLoadTestRun(run.id)
    runs.value = runs.value.map((item) => item.id === updated.id ? updated : item)
    selectedRun.value = updated
    success.value = '已提交取消请求，状态将自动刷新。'
    schedulePolling()
  } catch (reason) { setFeedback(getLoadTestingErrorMessage(reason)) }
  finally { cancelling.value = false }
}

watch(() => props.projectId, () => {
  detailController?.abort(); runsController?.abort(); environmentsController?.abort(); stopPolling(); draft.value = null
  runs.value = []; selectedRun.value = null
  void loadDefinitions()
  void loadActiveEnvironments()
}, { immediate: true })

onBeforeUnmount(() => {
  disposed = true
  listController?.abort(); detailController?.abort(); runsController?.abort(); environmentsController?.abort(); stopPolling()
})
</script>

<template>
  <section class="load-testing" aria-labelledby="load-testing-title">
    <header class="page-header">
      <div><h2 id="load-testing-title">压测中心</h2><p>配置多接口并发负载，实时跟踪吞吐量、成功率与响应耗时。</p></div>
      <button class="primary" type="button" :disabled="!projectId || projectsLoading" @click="createDraft">新建压测</button>
    </header>

    <div v-if="projectsLoading" class="state"><span class="loader"></span><strong>正在加载项目</strong></div>
    <div v-else-if="projectsError" class="state error-state"><strong>项目数据不可用</strong><p>{{ projectsError }}</p></div>
    <div v-else-if="!projectId" class="state"><strong>请先选择或新建项目</strong><p>压测配置按项目隔离，请使用右上角项目选择器。</p></div>
    <div v-else-if="loading" class="state"><span class="loader"></span><strong>正在读取压测配置</strong></div>
    <div v-else-if="error && !definitions.length && !draft" class="state error-state"><strong>压测服务暂时不可用</strong><p>{{ error }}</p><button class="secondary" type="button" @click="loadDefinitions">重新连接</button></div>
    <template v-else>
      <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>
      <p v-if="success" class="notice notice-success" role="status">{{ success }}</p>
      <div class="workspace">
        <aside class="definition-list">
          <div class="panel-title"><strong>压测配置</strong><span>{{ definitions.length }}</span></div>
          <button v-for="item in definitions" :key="item.id" type="button" class="definition-item" :class="{ active: draft?.id === item.id }" @click="chooseDefinition(item)">
            <strong>{{ item.name }}</strong><span>{{ item.traffic_mode === 'SCENARIO' ? '业务场景' : '独立接口' }} · {{ item.mode === 'COUNT' ? `按次数 · ${item.request_count === 1 ? '单次' : item.request_count + ' 次'}` : `按时间 · ${item.duration_seconds} 秒` }}</span><small>{{ item.targets.length }} 个{{ item.traffic_mode === 'SCENARIO' ? '步骤' : '目标' }} · {{ formatDate(item.updated_at) }}</small>
          </button>
          <div v-if="!definitions.length" class="empty-small">暂无压测配置<br>点击“新建压测”开始</div>
        </aside>

        <div v-if="draft" class="editor" @input="markDirty" @change="markDirty">
          <div class="editor-toolbar">
            <div class="title-fields"><input v-model="draft.name" maxlength="255" placeholder="压测名称" aria-label="压测名称"><input v-model="draft.description" maxlength="100000" placeholder="添加描述" aria-label="压测描述"></div>
            <div class="toolbar-actions"><span v-if="detailLoading">同步中…</span><span v-else-if="dirty" class="dirty">未保存</span><button class="secondary" type="button" @click="copyDraft">复制</button><button v-if="draft.id" class="secondary danger" type="button" :disabled="deleting" @click="removeDefinition">{{ deleting ? '删除中…' : '删除' }}</button><button class="secondary" type="button" :disabled="saving" @click="persist()">{{ saving ? '保存中…' : '保存' }}</button><button class="primary" type="button" :disabled="starting || saving" @click="startRun">{{ starting ? '启动中…' : '▶ 开始压测' }}</button></div>
          </div>

          <section class="environment-card">
            <label><span>运行环境（可选）</span><select v-model="draft.environmentId" :disabled="environmentsLoading"><option value="">不使用环境（目标填写完整 URL）</option><option v-for="environment in environments" :key="environment.id" :value="environment.id">{{ environment.name }}</option></select></label>
            <small v-if="environmentsLoading">正在加载当前项目的可用环境…</small><small v-else-if="environmentsError" class="environment-error">{{ environmentsError }}</small><p>平台运行时自动注入 Token，无需在请求头重复配置。</p>
          </section>

          <section class="settings-card">
            <h3>执行策略</h3>
            <div class="mode-row traffic-mode-row">
              <label class="mode-card" :class="{ active: draft.trafficMode === 'REQUESTS' }"><input v-model="draft.trafficMode" type="radio" value="REQUESTS"><span><strong>独立接口</strong><small>并发请求各接口，接口之间不共享变量</small></span></label>
              <label class="mode-card" :class="{ active: draft.trafficMode === 'SCENARIO' }"><input v-model="draft.trafficMode" type="radio" value="SCENARIO"><span><strong>业务场景</strong><small>每个虚拟用户按顺序执行一轮完整接口链</small></span></label>
            </div>
            <div class="mode-row">
              <label class="mode-card" :class="{ active: draft.mode === 'COUNT' }"><input v-model="draft.mode" type="radio" value="COUNT"><span><strong>按次数</strong><small>发送固定数量的请求；1 表示单次</small></span></label>
              <label class="mode-card" :class="{ active: draft.mode === 'DURATION' }"><input v-model="draft.mode" type="radio" value="DURATION"><span><strong>按时间</strong><small>在指定持续时间内发送请求</small></span></label>
            </div>
            <div class="number-grid">
              <label v-if="draft.mode === 'COUNT'"><span>{{ draft.trafficMode === 'SCENARIO' ? '场景次数' : '请求次数' }}</span><input v-model.number="draft.requestCount" type="number" min="1" max="10000"><small>{{ draft.requestCount === 1 ? '单次' : `共 ${draft.requestCount} 次` }}</small></label>
              <label v-else><span>持续时间（秒）</span><input v-model.number="draft.durationSeconds" type="number" min="1" max="1800"></label>
              <label><span>{{ draft.trafficMode === 'SCENARIO' ? '虚拟用户数' : '并发数' }}</span><input v-model.number="draft.concurrency" type="number" min="1" max="100"></label>
              <label><span>{{ draft.trafficMode === 'SCENARIO' ? '场景间隔' : '请求间隔' }}（毫秒）</span><input v-model.number="draft.intervalMs" type="number" min="0" max="60000"></label>
              <label><span>单请求超时（秒）</span><input v-model.number="draft.timeoutSeconds" type="number" min="0.1" max="120" step="0.1"></label>
            </div>
            <div v-if="draft.trafficMode === 'SCENARIO'" class="scenario-options">
              <label><span>初始变量 JSON</span><textarea v-model="draft.initialVariables" rows="4" spellcheck="false" placeholder='{"tenantId":"demo"}'></textarea><small>每轮场景开始时提供给所有步骤。</small></label>
              <label class="switch-field"><input v-model="draft.stopOnFailure" type="checkbox"><span><strong>步骤失败后终止本轮</strong><small>当前步骤失败时，不再执行本轮后续步骤。</small></span></label>
            </div>
          </section>

          <section class="targets-section">
            <header><div><h3>{{ draft.trafficMode === 'SCENARIO' ? '场景步骤' : '接口目标' }}</h3><p>{{ draft.trafficMode === 'SCENARIO' ? '每个虚拟用户按顺序执行以下步骤，最多 20 个。' : '并发请求以下独立目标，最多可配置 20 个。' }}</p></div><button class="secondary" type="button" :disabled="draft.targets.length >= 20" @click="addTarget">+ 添加{{ draft.trafficMode === 'SCENARIO' ? '步骤' : '目标' }}</button></header>
            <article v-for="(target, index) in draft.targets" :key="target.key" class="target-card">
              <header><span>{{ index + 1 }}</span><input v-model="target.name" maxlength="255" aria-label="目标名称" placeholder="接口名称"><button v-if="draft.trafficMode === 'SCENARIO'" type="button" :disabled="index === 0" @click="moveTarget(index, -1)">上移</button><button v-if="draft.trafficMode === 'SCENARIO'" type="button" :disabled="index === draft.targets.length - 1" @click="moveTarget(index, 1)">下移</button><button type="button" :disabled="draft.targets.length === 1" @click="removeTarget(index)">删除</button></header>
              <div class="request-line"><select v-model="target.method" aria-label="请求方法"><option v-for="method in methods" :key="method">{{ method }}</option></select><input v-model="target.url" :type="draft.environmentId ? 'text' : 'url'" :placeholder="draft.environmentId ? '/api/resource' : 'https://api.example.com/resource'" aria-label="请求 URL"></div>
              <div class="json-grid"><label><span>请求头 JSON</span><textarea v-model="target.headers" rows="4" spellcheck="false"></textarea></label><label><span>查询参数 JSON</span><textarea v-model="target.query" rows="4" spellcheck="false"></textarea></label><label><span>请求体 JSON</span><textarea v-model="target.body" rows="4" spellcheck="false"></textarea></label><label><span>期望状态码</span><input v-model="target.expectedStatuses" placeholder="200, 201"><small>多个状态码用逗号分隔</small></label></div>
              <div v-if="draft.trafficMode === 'SCENARIO'" class="scenario-step-grid">
                <label><span>步骤等待（毫秒）</span><input v-model.number="target.thinkTimeMs" type="number" min="0" max="60000"><small>完成本步骤后再等待指定时间。</small></label>
                <label><span>响应提取 JSON</span><textarea v-model="target.extractors" rows="4" spellcheck="false" placeholder='{"orderId":"data.id"}'></textarea><small>值为响应 JSON 路径；后续可在 URL、Header、Query、Body 中使用 <code v-pre>{{orderId}}</code>。</small></label>
              </div>
            </article>
          </section>

          <section class="results-section">
            <header><div><h3>运行历史与指标</h3><span v-if="runsLoading">刷新中…</span></div><button type="button" :disabled="runsLoading || !draft.id" @click="loadRuns(draft.id)">刷新</button></header>
            <div class="history-layout">
              <div class="history-list"><button v-for="run in runs" :key="run.id" type="button" :class="{ active: selectedRun?.id === run.id }" @click.stop="selectedRun = run"><span class="status" :class="run.status.toLowerCase()">{{ statusLabels[run.status] }}</span><span>{{ run.mode === 'COUNT' ? '按次数' : '按时间' }}</span><small>{{ formatDate(run.started_at) }}</small></button><p v-if="!runsLoading && !runs.length">暂无运行记录</p></div>
              <div class="metrics-area">
                <div v-if="selectedRun" class="run-summary"><div><strong>{{ statusLabels[selectedRun.status] }}</strong><span>开始 {{ formatDate(selectedRun.started_at) }} · 完成 {{ formatDate(selectedRun.finished_at) }}</span><button v-if="selectedRun.status === 'RUNNING'" class="secondary danger" type="button" :disabled="cancelling" @click="cancelRun">{{ cancelling ? '取消中…' : '停止压测' }}</button></div><p v-if="selectedRun.error_message" class="run-error">{{ selectedRun.error_message }}</p></div>
                <div v-if="metrics && (selectedRun?.status !== 'RUNNING' || metrics.total_requests > 0)" class="metrics-content">
                  <div class="metric-cards"><div><span>总请求</span><strong>{{ metrics.total_requests }}</strong></div><div><span>成功请求</span><strong>{{ metrics.successful_requests }}</strong></div><div><span>失败请求</span><strong>{{ metrics.failed_requests }}</strong></div><div><span>成功率</span><strong>{{ successRate }}</strong></div><div><span>吞吐量</span><strong>{{ metrics.requests_per_second.toFixed(2) }} <small>RPS</small></strong></div><div><span>平均耗时</span><strong>{{ metrics.average_ms.toFixed(1) }} <small>ms</small></strong></div></div>
                  <div class="latency-row"><span>Min <strong>{{ metrics.min_ms.toFixed(1) }} ms</strong></span><span>P50 <strong>{{ metrics.p50_ms.toFixed(1) }} ms</strong></span><span>P95 <strong>{{ metrics.p95_ms.toFixed(1) }} ms</strong></span><span>P99 <strong>{{ metrics.p99_ms.toFixed(1) }} ms</strong></span><span>Max <strong>{{ metrics.max_ms.toFixed(1) }} ms</strong></span></div>
                  <template v-if="metrics.scenario_metrics && metrics.scenario_metrics.total_scenarios > 0">
                    <div class="scenario-metrics-title">场景指标</div>
                    <div class="metric-cards scenario-metric-cards"><div><span>场景轮数</span><strong>{{ metrics.scenario_metrics.total_scenarios }}</strong></div><div><span>场景成功率</span><strong>{{ scenarioSuccessRate }}</strong></div><div><span>场景 RPS</span><strong>{{ metrics.scenario_metrics.scenarios_per_second.toFixed(2) }}</strong></div><div><span>场景耗时</span><strong>{{ metrics.scenario_metrics.average_ms.toFixed(1) }} <small>ms</small></strong></div></div>
                    <div class="latency-row"><span>场景 Min <strong>{{ metrics.scenario_metrics.min_ms.toFixed(1) }} ms</strong></span><span>P50 <strong>{{ metrics.scenario_metrics.p50_ms.toFixed(1) }} ms</strong></span><span>P95 <strong>{{ metrics.scenario_metrics.p95_ms.toFixed(1) }} ms</strong></span><span>P99 <strong>{{ metrics.scenario_metrics.p99_ms.toFixed(1) }} ms</strong></span><span>Max <strong>{{ metrics.scenario_metrics.max_ms.toFixed(1) }} ms</strong></span></div>
                  </template>
                  <div class="counts-grid"><div><h4>状态码分布</h4><p v-if="!Object.keys(metrics.status_counts).length">暂无</p><span v-for="(count, code) in metrics.status_counts" :key="code"><code>{{ code }}</code>{{ count }}</span></div><div><h4>错误分布</h4><p v-if="!Object.keys(metrics.error_counts).length">暂无</p><span v-for="(count, name) in metrics.error_counts" :key="name"><code>{{ name }}</code>{{ count }}</span></div></div>
                  <div v-if="targetMetricRows.length" class="target-table-wrap"><h4>目标明细</h4><table><thead><tr><th>目标</th><th>请求</th><th>成功</th><th>失败</th><th>RPS</th><th>平均 ms</th><th>P95 ms</th></tr></thead><tbody><tr v-for="row in targetMetricRows" :key="row.name"><td>{{ row.name }}</td><td>{{ row.total ?? '—' }}</td><td>{{ row.successful ?? '—' }}</td><td>{{ row.failed ?? '—' }}</td><td>{{ formatNumber(row.rps, 2) }}</td><td>{{ formatNumber(row.average) }}</td><td>{{ formatNumber(row.p95) }}</td></tr></tbody></table></div>
                </div>
                <div v-else class="result-empty">{{ selectedRun?.status === 'RUNNING' ? '压测运行中，指标将自动刷新…' : selectedRun ? '本次运行暂无指标' : '选择运行记录查看指标' }}</div>
              </div>
            </div>
          </section>
        </div>
        <div v-else class="editor-empty"><strong>还没有压测配置</strong><p>新建配置后即可添加接口目标并启动压测。</p><button class="primary" type="button" @click="createDraft">新建压测</button></div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.load-testing{display:flex;flex-direction:column;gap:16px}.page-header{display:flex;align-items:center;justify-content:space-between;gap:16px}.page-header h2{margin:0;color:#1c273a;font-size:20px}.page-header p,.targets-section>header p{margin:6px 0 0;color:#8994a7;font-size:12px}.primary,.secondary{border-radius:8px;font-weight:700;cursor:pointer}.primary{height:40px;padding:0 17px;border:0;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc)}.secondary{height:34px;padding:0 12px;border:1px solid #dfe5ee;color:#556176;background:#fff}.primary:disabled,.secondary:disabled{cursor:not-allowed;opacity:.55}.secondary.danger{color:#bd4654}.state{min-height:260px;padding:28px;border:1px solid #e5eaf2;border-radius:14px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;text-align:center;background:#fff}.state p{max-width:440px;margin:0;color:#8994a7;font-size:12px}.error-state{border-color:#ffd7db}.loader{width:20px;height:20px;border:3px solid #dbe4f5;border-top-color:#4f72ca;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.notice{margin:0;padding:10px 12px;border-radius:8px;font-size:12px}.notice-error{border:1px solid #ffd7db;color:#b84350;background:#fff5f6}.notice-success{border:1px solid #ccebd8;color:#287a49;background:#f2fbf5}.workspace{min-height:600px;display:grid;grid-template-columns:220px minmax(0,1fr);border:1px solid #e3e8f0;border-radius:14px;overflow:hidden;background:#fff}.definition-list{padding:14px;border-right:1px solid #e8edf4;background:#f8fafc}.panel-title{min-height:26px;display:flex;align-items:center;justify-content:space-between;color:#344158;font-size:12px}.panel-title span{color:#8994a7;font-size:10px}.definition-item{width:100%;display:grid;gap:4px;margin-top:7px;padding:11px;border:1px solid transparent;border-radius:9px;text-align:left;background:transparent;cursor:pointer}.definition-item:hover{background:#fff}.definition-item.active{border-color:#cfdbf8;background:#fff;box-shadow:0 3px 10px rgba(54,79,136,.08)}.definition-item strong{overflow:hidden;color:#344158;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.definition-item span{color:#5c76bb;font-size:9px}.definition-item small{color:#929daf;font-size:9px}.empty-small,.editor-empty{color:#9aa4b4;text-align:center;font-size:10px;line-height:1.7}.empty-small{margin-top:18px}.editor{min-width:0;background:#f7f9fc}.editor-toolbar{min-height:70px;padding:11px 15px;border-bottom:1px solid #e8edf4;display:flex;align-items:center;justify-content:space-between;gap:12px;background:#fff}.title-fields{min-width:180px;display:grid;gap:4px}.title-fields input{padding:3px 5px;border:1px solid transparent;border-radius:5px;background:transparent}.title-fields input:first-child{color:#243149;font-size:15px;font-weight:750}.title-fields input:last-child{color:#8792a5;font-size:10px}.title-fields input:focus{border-color:#b8c9ef;outline:0}.toolbar-actions{display:flex;align-items:center;gap:7px}.toolbar-actions>span,.dirty{color:#8994a7;font-size:10px;white-space:nowrap}.toolbar-actions .dirty{color:#b47a24}.environment-card,.settings-card,.targets-section,.results-section{margin:14px;border:1px solid #e3e8f0;border-radius:11px;background:#fff}.environment-card,.settings-card,.targets-section{padding:15px}.environment-card label{display:grid;gap:6px;color:#5f6c81;font-size:10px}.environment-card select{height:38px;padding:0 10px;border:1px solid #dfe5ee;border-radius:7px;color:#29364a;background:#fbfcfe;font:500 11px inherit}.environment-card>small{display:block;margin-top:6px;color:#8994a7;font-size:9px}.environment-card>small.environment-error{color:#b84350}.environment-card p{margin:10px 0 0;padding:8px 10px;border-radius:7px;color:#456cbf;background:#f1f5ff;font-size:10px}.settings-card h3,.targets-section h3,.results-section h3{margin:0;color:#344158;font-size:13px}.mode-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:13px}.mode-card{padding:12px;border:1px solid #e0e6ef;border-radius:9px;display:flex;align-items:flex-start;gap:9px;cursor:pointer}.mode-card.active{border-color:#7392df;background:#f5f8ff;box-shadow:0 0 0 2px #e9efff}.mode-card input{margin-top:3px}.mode-card span{display:grid;gap:3px}.mode-card strong{color:#354158;font-size:11px}.mode-card small{color:#8e99ab;font-size:9px}.number-grid{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:10px;margin-top:13px}.number-grid label,.json-grid label{display:grid;align-content:start;gap:5px;color:#5f6c81;font-size:10px}.number-grid input,.json-grid input,.request-line input,.request-line select,.target-card>header input{height:35px;padding:0 9px;border:1px solid #dfe5ee;border-radius:7px;color:#29364a;background:#fbfcfe;font:500 11px inherit}.number-grid small,.json-grid small{color:#929daf;font-size:9px}.targets-section>header,.results-section>header{display:flex;align-items:center;justify-content:space-between;gap:12px}.target-card{margin-top:13px;border:1px solid #e1e7ef;border-radius:10px;overflow:hidden}.target-card>header{padding:9px 11px;border-bottom:1px solid #edf0f5;display:flex;align-items:center;gap:8px;background:#fafbfd}.target-card>header>span{width:23px;height:23px;border-radius:6px;display:grid;place-items:center;color:#fff;background:#5d7bd1;font-size:10px;font-weight:800}.target-card>header input{flex:1;border-color:transparent;background:transparent;font-weight:700}.target-card>header button{border:0;color:#bd4654;background:transparent;font-size:10px;font-weight:700;cursor:pointer}.target-card>header button:disabled{color:#b9c0cc;cursor:not-allowed}.request-line{padding:11px;display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px}.request-line select{font-weight:750}.json-grid{padding:0 11px 12px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.json-grid textarea{width:100%;padding:8px;border:1px solid #dfe5ee;border-radius:7px;resize:vertical;color:#29364a;background:#fbfcfe;font:10px/1.45 Consolas,"Microsoft YaHei",monospace}.number-grid input:focus,.json-grid input:focus,.json-grid textarea:focus,.request-line input:focus,.request-line select:focus{border-color:#6b8de2;outline:0}.results-section{overflow:hidden}.results-section>header{padding:13px 15px}.results-section>header>div{display:flex;align-items:center;gap:8px}.results-section>header span{color:#8f9aac;font-size:9px}.results-section>header button{border:0;color:#5071c7;background:transparent;font-size:10px;font-weight:700;cursor:pointer}.history-layout{min-height:300px;display:grid;grid-template-columns:210px minmax(0,1fr);border-top:1px solid #edf0f5}.history-list{max-height:460px;padding:9px;overflow:auto;border-right:1px solid #edf0f5;background:#fafbfd}.history-list button{width:100%;padding:9px 8px;border:1px solid transparent;border-radius:7px;display:grid;grid-template-columns:1fr 1fr;gap:5px;text-align:left;background:transparent;cursor:pointer}.history-list button.active{border-color:#d8e2f8;background:#fff}.history-list button>span:nth-child(2){color:#7d899d;text-align:right;font-size:9px}.history-list small{grid-column:1/-1;color:#929daf;font-size:9px}.history-list p{color:#929daf;text-align:center;font-size:9px}.status{font-size:9px;font-weight:800}.status.running{color:#4d70ca}.status.succeeded{color:#278555}.status.failed{color:#bf4654}.metrics-area{min-width:0;padding:14px}.run-summary>div{display:flex;justify-content:space-between;gap:12px;color:#344158;font-size:11px}.run-summary span{color:#929daf;font-size:9px}.run-error{padding:8px;border-radius:6px;color:#b84350;background:#fff4f5;font-size:10px}.metric-cards{display:grid;grid-template-columns:repeat(3,minmax(100px,1fr));gap:9px;margin-top:12px}.metric-cards>div{padding:11px;border:1px solid #e7ebf2;border-radius:8px;display:grid;gap:5px;background:#fafbfd}.metric-cards span{color:#8490a3;font-size:9px}.metric-cards strong{color:#2d3b53;font-size:17px}.metric-cards small{font-size:8px}.latency-row{margin-top:10px;padding:10px;border-radius:8px;display:flex;justify-content:space-around;gap:8px;flex-wrap:wrap;background:#f4f7fb}.latency-row span{color:#8490a3;font-size:9px}.latency-row strong{margin-left:4px;color:#43516a}.counts-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}.counts-grid>div{padding:10px;border:1px solid #e7ebf2;border-radius:8px;display:flex;align-content:flex-start;gap:6px;flex-wrap:wrap}.counts-grid h4,.target-table-wrap h4{width:100%;margin:0 0 4px;color:#566278;font-size:10px}.counts-grid span{padding:4px 7px;border-radius:5px;color:#647188;background:#f5f7fa;font-size:9px}.counts-grid code{margin-right:6px;color:#41516c;font-weight:700}.counts-grid p{margin:0;color:#9aa4b4;font-size:9px}.target-table-wrap{margin-top:10px;overflow:auto}.target-table-wrap table{width:100%;border-collapse:collapse;color:#5e6b80;font-size:9px}.target-table-wrap th,.target-table-wrap td{padding:7px;border-bottom:1px solid #edf0f5;text-align:right;white-space:nowrap}.target-table-wrap th:first-child,.target-table-wrap td:first-child{text-align:left}.result-empty,.editor-empty{min-height:250px;display:flex;flex-direction:column;align-items:center;justify-content:center}.editor-empty p{margin:4px 0 12px}.editor-empty strong{color:#637086;font-size:13px}
.traffic-mode-row+.mode-row{margin-top:10px}.scenario-options{margin-top:13px;padding:12px;border-radius:9px;display:grid;grid-template-columns:minmax(0,1fr) minmax(220px,1fr);gap:14px;background:#f7f9fd}.scenario-options>label,.scenario-step-grid label{display:grid;align-content:start;gap:5px;color:#5f6c81;font-size:10px}.scenario-options textarea,.scenario-step-grid textarea{width:100%;box-sizing:border-box;padding:8px;border:1px solid #dfe5ee;border-radius:7px;resize:vertical;color:#29364a;background:#fff;font:10px/1.45 Consolas,"Microsoft YaHei",monospace}.scenario-options small,.scenario-step-grid small{color:#929daf;font-size:9px}.scenario-options .switch-field{display:flex;align-items:flex-start;padding:10px;border:1px solid #e3e8f0;border-radius:8px;background:#fff}.switch-field>span{display:grid;gap:4px}.switch-field strong{color:#45536a;font-size:10px}.scenario-step-grid{padding:0 11px 12px;display:grid;grid-template-columns:minmax(150px,1fr) minmax(0,2fr);gap:10px}.scenario-step-grid input{height:35px;padding:0 9px;border:1px solid #dfe5ee;border-radius:7px;color:#29364a;background:#fbfcfe;font:500 11px inherit}.scenario-options textarea:focus,.scenario-step-grid textarea:focus,.scenario-step-grid input:focus{border-color:#6b8de2;outline:0}.scenario-step-grid code{color:#456cbf}.scenario-metrics-title{margin-top:14px;color:#566278;font-size:11px;font-weight:800}.scenario-metric-cards{grid-template-columns:repeat(4,minmax(100px,1fr));margin-top:8px}
@media(max-width:1100px){.workspace{grid-template-columns:185px minmax(0,1fr)}.editor-toolbar{align-items:flex-start;flex-direction:column}.toolbar-actions{width:100%;overflow-x:auto}.number-grid{grid-template-columns:repeat(2,minmax(120px,1fr))}.metric-cards{grid-template-columns:repeat(2,minmax(100px,1fr))}}
@media(max-width:760px){.page-header{align-items:flex-start}.workspace{display:block}.definition-list{max-height:220px;overflow:auto;border-right:0;border-bottom:1px solid #e8edf4}.mode-row,.json-grid,.counts-grid,.scenario-options,.scenario-step-grid{grid-template-columns:1fr}.number-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.history-layout{grid-template-columns:1fr}.history-list{max-height:170px;border-right:0;border-bottom:1px solid #edf0f5}.run-summary>div{flex-direction:column}.settings-card,.targets-section,.results-section{margin:10px}.request-line{grid-template-columns:90px minmax(0,1fr)}}
@media(max-width:480px){.page-header{flex-direction:column}.page-header .primary{width:100%}.number-grid,.metric-cards{grid-template-columns:1fr}.targets-section>header{align-items:flex-start;flex-direction:column}.toolbar-actions .secondary{flex:0 0 auto}.metrics-area{padding:10px}}
</style>