import type {
  HttpMethod, JsonObject, JsonValue, LoadTestCreateInput, LoadTestDefinition,
  LoadTestMetrics, LoadTestMode, LoadTestRun, LoadTestRunStatus, LoadTestTarget,
  LoadTestTrafficMode, LoadTestUpdateInput,
} from '../types/load-testing'

type UnknownRecord = Record<string, unknown>
const modes: readonly LoadTestMode[] = ['COUNT', 'DURATION']
const trafficModes: readonly LoadTestTrafficMode[] = ['REQUESTS', 'SCENARIO']
const statuses: readonly LoadTestRunStatus[] = ['RUNNING', 'SUCCEEDED', 'FAILED']
const methods: readonly HttpMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isJsonValue)
  return isRecord(value) && Object.values(value).every(isJsonValue)
}

function isJsonObject(value: unknown): value is JsonObject {
  return isRecord(value) && Object.values(value).every(isJsonValue)
}

function isDateTime(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function isInteger(value: unknown, minimum: number): value is number {
  return Number.isInteger(value) && (value as number) >= minimum
}

function isFiniteNumber(value: unknown, minimum = 0): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= minimum
}

function isNumberMap(value: unknown): value is Record<string, number> {
  return isRecord(value) && Object.values(value).every((item) => isFiniteNumber(item))
}

function isStringMap(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === 'string')
}

function parseTarget(value: unknown): LoadTestTarget | null {
  if (!isRecord(value) || typeof value.name !== 'string' || !value.name.trim()
    || !methods.includes(value.method as HttpMethod) || typeof value.url !== 'string' || !value.url.trim()
    || !isJsonObject(value.headers) || !isJsonObject(value.query) || !isJsonValue(value.body)
    || !Array.isArray(value.expected_statuses) || value.expected_statuses.length === 0
    || !value.expected_statuses.every((item) => isInteger(item, 100) && item <= 599)) return null
  const extractors = value.extractors === undefined ? {} : value.extractors
  const thinkTimeMs = value.think_time_ms === undefined ? 0 : value.think_time_ms
  if (!isStringMap(extractors) || !isInteger(thinkTimeMs, 0)) return null
  return { name: value.name, method: value.method as HttpMethod, url: value.url,
    headers: value.headers, query: value.query, body: value.body,
    expected_statuses: value.expected_statuses as number[], extractors, think_time_ms: thinkTimeMs }
}

function parseDefinition(value: unknown): LoadTestDefinition | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.project_id !== 'string' || !value.project_id
    || !(value.environment_id === null || (typeof value.environment_id === 'string' && value.environment_id.length > 0))
    || typeof value.name !== 'string' || typeof value.description !== 'string'
    || !Array.isArray(value.targets) || value.targets.length < 1 || value.targets.length > 20
    || !modes.includes(value.mode as LoadTestMode) || !isInteger(value.request_count, 1)
    || !isInteger(value.duration_seconds, 1) || !isInteger(value.concurrency, 1)
    || !isInteger(value.interval_ms, 0) || !isFiniteNumber(value.timeout_seconds, Number.MIN_VALUE)
    || !isInteger(value.state_version, 0) || !isDateTime(value.created_at)
    || !isDateTime(value.updated_at)) return null
  const trafficMode = value.traffic_mode === undefined ? 'REQUESTS' : value.traffic_mode
  const initialVariables = value.initial_variables === undefined ? {} : value.initial_variables
  const stopOnFailure = value.stop_on_failure === undefined ? true : value.stop_on_failure
  if (!trafficModes.includes(trafficMode as LoadTestTrafficMode)
    || !isJsonObject(initialVariables) || typeof stopOnFailure !== 'boolean') return null
  const targets = value.targets.map(parseTarget)
  if (targets.some((target) => target === null)) return null
  return { id: value.id, project_id: value.project_id,
    environment_id: value.environment_id as string | null,
    name: value.name,
    description: value.description, targets: targets as LoadTestTarget[],
    traffic_mode: trafficMode as LoadTestTrafficMode,
    initial_variables: initialVariables, stop_on_failure: stopOnFailure,
    mode: value.mode as LoadTestMode, request_count: value.request_count,
    duration_seconds: value.duration_seconds, concurrency: value.concurrency,
    interval_ms: value.interval_ms, timeout_seconds: value.timeout_seconds,
    state_version: value.state_version, created_at: value.created_at, updated_at: value.updated_at }
}

function parseMetrics(value: unknown): LoadTestMetrics | null {
  if (!isRecord(value) || !isInteger(value.total_requests, 0)
    || !isInteger(value.successful_requests, 0) || !isInteger(value.failed_requests, 0)
    || !isFiniteNumber(value.requests_per_second) || !isFiniteNumber(value.average_ms)
    || !isFiniteNumber(value.min_ms) || !isFiniteNumber(value.max_ms)
    || !isFiniteNumber(value.p50_ms) || !isFiniteNumber(value.p95_ms)
    || !isFiniteNumber(value.p99_ms) || !isNumberMap(value.status_counts)
    || !isNumberMap(value.error_counts) || !isRecord(value.target_metrics)) return null
  const targetMetrics: LoadTestMetrics['target_metrics'] = {}
  for (const [name, item] of Object.entries(value.target_metrics)) {
    if (!isRecord(item) || !isInteger(item.total_requests, 0)
      || !isInteger(item.successful_requests, 0) || !isInteger(item.failed_requests, 0)
      || !isFiniteNumber(item.requests_per_second) || !isFiniteNumber(item.average_ms)
      || !isFiniteNumber(item.min_ms) || !isFiniteNumber(item.max_ms)
      || !isFiniteNumber(item.p50_ms) || !isFiniteNumber(item.p95_ms)
      || !isFiniteNumber(item.p99_ms) || !isNumberMap(item.status_counts)
      || !isNumberMap(item.error_counts)) return null
    targetMetrics[name] = {
      total_requests: item.total_requests, successful_requests: item.successful_requests,
      failed_requests: item.failed_requests, requests_per_second: item.requests_per_second,
      average_ms: item.average_ms, min_ms: item.min_ms, max_ms: item.max_ms,
      p50_ms: item.p50_ms, p95_ms: item.p95_ms, p99_ms: item.p99_ms,
      status_counts: item.status_counts, error_counts: item.error_counts,
    }
  }
  const rawScenarioMetrics = value.scenario_metrics
  let scenarioMetrics: LoadTestMetrics['scenario_metrics'] = null
  if (rawScenarioMetrics !== undefined && rawScenarioMetrics !== null) {
    if (!isRecord(rawScenarioMetrics) || !isInteger(rawScenarioMetrics.total_scenarios, 0)
      || !isInteger(rawScenarioMetrics.successful_scenarios, 0)
      || !isInteger(rawScenarioMetrics.failed_scenarios, 0)
      || !isFiniteNumber(rawScenarioMetrics.scenarios_per_second)
      || !isFiniteNumber(rawScenarioMetrics.average_ms) || !isFiniteNumber(rawScenarioMetrics.min_ms)
      || !isFiniteNumber(rawScenarioMetrics.max_ms) || !isFiniteNumber(rawScenarioMetrics.p50_ms)
      || !isFiniteNumber(rawScenarioMetrics.p95_ms) || !isFiniteNumber(rawScenarioMetrics.p99_ms)) return null
    scenarioMetrics = {
      total_scenarios: rawScenarioMetrics.total_scenarios,
      successful_scenarios: rawScenarioMetrics.successful_scenarios,
      failed_scenarios: rawScenarioMetrics.failed_scenarios,
      scenarios_per_second: rawScenarioMetrics.scenarios_per_second,
      average_ms: rawScenarioMetrics.average_ms, min_ms: rawScenarioMetrics.min_ms,
      max_ms: rawScenarioMetrics.max_ms, p50_ms: rawScenarioMetrics.p50_ms,
      p95_ms: rawScenarioMetrics.p95_ms, p99_ms: rawScenarioMetrics.p99_ms,
    }
  }
  return { total_requests: value.total_requests, successful_requests: value.successful_requests,
    failed_requests: value.failed_requests, requests_per_second: value.requests_per_second,
    average_ms: value.average_ms, min_ms: value.min_ms, max_ms: value.max_ms,
    p50_ms: value.p50_ms, p95_ms: value.p95_ms, p99_ms: value.p99_ms,
    status_counts: value.status_counts, error_counts: value.error_counts,
    target_metrics: targetMetrics, scenario_metrics: scenarioMetrics }
}

function parseRun(value: unknown): LoadTestRun | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.load_test_id !== 'string' || !value.load_test_id
    || !statuses.includes(value.status as LoadTestRunStatus)
    || !modes.includes(value.mode as LoadTestMode) || !isDateTime(value.started_at)
    || !(value.finished_at === null || isDateTime(value.finished_at))
    || !(value.error_message === null || typeof value.error_message === 'string')) return null
  const metrics = value.metrics === null ? null : parseMetrics(value.metrics)
  if (value.metrics !== null && metrics === null) return null
  return { id: value.id, load_test_id: value.load_test_id,
    status: value.status as LoadTestRunStatus, mode: value.mode as LoadTestMode,
    started_at: value.started_at, finished_at: value.finished_at,
    metrics, error_message: value.error_message }
}

async function httpError(response: Response): Promise<Error> {
  const payload: unknown = await response.json().catch(() => null)
  const code = isRecord(payload) && typeof payload.code === 'string' ? payload.code : ''
  const detail = isRecord(payload) && typeof payload.detail === 'string' ? payload.detail.trim() : ''
  if (response.status === 401) return new Error('登录会话已失效，请重新登录。')
  if (response.status === 404) return new Error('未找到该压测配置或执行记录。')
  if (code.includes('version_conflict')) return new Error('压测配置已被其他操作修改，请刷新后重试。')
  if (response.status === 409) return new Error('当前压测配置状态不允许此操作。')
  if (response.status === 422 || code === 'validation_error') return new Error('压测配置校验失败，请检查填写内容。')
  return new Error(detail || `压测服务请求失败（HTTP ${response.status}）。`)
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) } })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await httpError(response)
  return response
}

async function readDefinition(response: Response, action: string): Promise<LoadTestDefinition> {
  const payload: unknown = await response.json().catch(() => { throw new Error(`${action}未返回有效 JSON`) })
  const definition = parseDefinition(payload)
  if (!definition) throw new Error(`${action}响应格式暂不支持`)
  return definition
}

async function readRun(response: Response, action: string): Promise<LoadTestRun> {
  const payload: unknown = await response.json().catch(() => { throw new Error(`${action}未返回有效 JSON`) })
  const run = parseRun(payload)
  if (!run) throw new Error(`${action}响应格式暂不支持`)
  return run
}

function readItems(payload: unknown): unknown[] | null {
  if (Array.isArray(payload)) return payload
  return isRecord(payload) && Array.isArray(payload.items) ? payload.items : null
}

export async function fetchLoadTests(projectId: string, signal?: AbortSignal): Promise<LoadTestDefinition[]> {
  const response = await request(`/api/v1/load-tests?project_id=${encodeURIComponent(projectId)}`, { signal })
  const payload: unknown = await response.json().catch(() => { throw new Error('压测配置列表未返回有效 JSON') })
  const items = readItems(payload)
  if (!items) throw new Error('压测配置列表响应格式暂不支持')
  const definitions = items.map(parseDefinition)
  if (definitions.some((item) => item === null)) throw new Error('压测配置记录字段格式暂不支持')
  return definitions as LoadTestDefinition[]
}

export async function getLoadTest(id: string, signal?: AbortSignal): Promise<LoadTestDefinition> {
  return readDefinition(await request(`/api/v1/load-tests/${encodeURIComponent(id)}`, { signal }), '读取压测配置')
}

export async function createLoadTest(input: LoadTestCreateInput): Promise<LoadTestDefinition> {
  const response = await request('/api/v1/load-tests', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  return readDefinition(response, '创建压测配置')
}

export async function updateLoadTest(id: string, input: LoadTestUpdateInput): Promise<LoadTestDefinition> {
  const response = await request(`/api/v1/load-tests/${encodeURIComponent(id)}`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  return readDefinition(response, '保存压测配置')
}

export async function deleteLoadTest(id: string): Promise<void> {
  await request(`/api/v1/load-tests/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function startLoadTestRun(id: string): Promise<LoadTestRun> {
  return readRun(await request(`/api/v1/load-tests/${encodeURIComponent(id)}/runs`, { method: 'POST' }), '启动压测')
}

export async function cancelLoadTestRun(runId: string): Promise<LoadTestRun> {
  return readRun(await request(`/api/v1/load-test-runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
  }), '取消压测')
}

export async function fetchLoadTestRuns(id: string, signal?: AbortSignal): Promise<LoadTestRun[]> {
  const response = await request(`/api/v1/load-tests/${encodeURIComponent(id)}/runs`, { signal })
  const payload: unknown = await response.json().catch(() => { throw new Error('压测历史未返回有效 JSON') })
  const items = readItems(payload)
  if (!items) throw new Error('压测历史响应格式暂不支持')
  const runs = items.map(parseRun)
  if (runs.some((item) => item === null)) throw new Error('压测历史记录字段格式暂不支持')
  return runs as LoadTestRun[]
}

export async function getLoadTestRun(runId: string, signal?: AbortSignal): Promise<LoadTestRun> {
  return readRun(await request(`/api/v1/load-test-runs/${encodeURIComponent(runId)}`, { signal }), '读取压测结果')
}

export function getLoadTestingErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源压测服务'
  if (reason instanceof Error) return reason.message
  return '请求压测服务时发生未知错误'
}