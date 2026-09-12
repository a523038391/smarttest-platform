import type {
  ExecutionBatch,
  JsonRecord,
  PlanExecutionOptions,
  TestPlanCreateInput,
  TestPlanDataRow,
  TestPlanItem,
  TestPlanResponse,
  TestPlanStatus,
  TestPlanUpdateInput,
} from '../types/test-plan'

const planStatuses: readonly TestPlanStatus[] = ['DRAFT', 'ACTIVE', 'ARCHIVED']

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isDateTime(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function isInteger(value: unknown, minimum: number): value is number {
  return Number.isInteger(value) && (value as number) >= minimum
}

function isStatus(value: unknown): value is TestPlanStatus {
  return typeof value === 'string' && planStatuses.includes(value as TestPlanStatus)
}

function parseDataRow(value: unknown): TestPlanDataRow | null {
  if (!isRecord(value) || typeof value.row_key !== 'string' || !value.row_key
    || !isRecord(value.values)) return null
  return { row_key: value.row_key, values: value.values }
}

function parseItem(value: unknown): TestPlanItem | null {
  if (!isRecord(value) || typeof value.item_id !== 'string' || !value.item_id
    || typeof value.script_id !== 'string' || !value.script_id
    || !isInteger(value.script_revision, 1)
    || !(value.environment_id === null || typeof value.environment_id === 'string')
    || !(value.environment_revision === null || isInteger(value.environment_revision, 1))
    || !isRecord(value.default_parameters) || !Array.isArray(value.data_rows)
    || !isRecord(value.execution_policy)) return null
  if ((value.environment_id === null) !== (value.environment_revision === null)) return null
  const rows = value.data_rows.map(parseDataRow)
  if (rows.some((row) => row === null)) return null
  return {
    item_id: value.item_id,
    script_id: value.script_id,
    script_revision: value.script_revision,
    environment_id: value.environment_id,
    environment_revision: value.environment_revision,
    default_parameters: value.default_parameters,
    data_rows: rows.filter((row): row is TestPlanDataRow => row !== null),
    execution_policy: value.execution_policy,
  }
}

function parsePlan(value: unknown): TestPlanResponse | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.tenant_id !== 'string' || !value.tenant_id
    || typeof value.project_id !== 'string' || !value.project_id
    || typeof value.name !== 'string' || typeof value.description !== 'string'
    || !Array.isArray(value.items) || value.items.length === 0
    || !isInteger(value.max_parallel, 1) || value.max_parallel > 100
    || !isStatus(value.status) || !isInteger(value.revision, 1)
    || !isInteger(value.state_version, 0) || !isDateTime(value.created_at)
    || !isDateTime(value.updated_at)) return null
  const items = value.items.map(parseItem)
  if (items.some((item) => item === null)) return null
  return {
    id: value.id, tenant_id: value.tenant_id, project_id: value.project_id,
    name: value.name, description: value.description,
    items: items.filter((item): item is TestPlanItem => item !== null),
    max_parallel: value.max_parallel, status: value.status, revision: value.revision,
    state_version: value.state_version, created_at: value.created_at, updated_at: value.updated_at,
  }
}

function parseBatch(value: unknown): ExecutionBatch | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.tenant_id !== 'string' || !value.tenant_id
    || typeof value.project_id !== 'string' || !value.project_id
    || typeof value.plan_id !== 'string' || !value.plan_id
    || !isInteger(value.plan_revision, 1) || typeof value.idempotency_key !== 'string'
    || !value.idempotency_key || !isInteger(value.max_parallel, 1) || value.max_parallel > 100
    || !Array.isArray(value.run_ids) || value.run_ids.length === 0
    || !value.run_ids.every((id) => typeof id === 'string' && id)
    || !isDateTime(value.created_at)) return null
  return {
    id: value.id, tenant_id: value.tenant_id, project_id: value.project_id,
    plan_id: value.plan_id, plan_revision: value.plan_revision,
    idempotency_key: value.idempotency_key, max_parallel: value.max_parallel,
    run_ids: value.run_ids as string[], created_at: value.created_at,
  }
}

async function createHttpError(response: Response): Promise<Error> {
  let code = ''
  if (response.headers.get('content-type')?.includes('application/problem+json')) {
    const payload: unknown = await response.json().catch(() => null)
    if (isRecord(payload) && typeof payload.code === 'string') code = payload.code
  }
  if (response.status === 401) return new Error('登录会话已失效，请重新登录。')
  if (code === 'test_plan_not_found') return new Error('未找到该测试计划。')
  if (code === 'test_plan_version_conflict') return new Error('计划已被其他操作修改，请刷新后重试。')
  if (code === 'test_plan_conflict') return new Error('计划当前状态不允许此操作，请刷新后重试。')
  if (code === 'validation_error') return new Error('测试计划信息校验失败，请检查填写内容。')
  if (code === 'dispatch_unavailable') return new Error('执行调度服务暂时不可用，请稍后重试。')
  return new Error(`测试计划服务请求失败（HTTP ${response.status}）。`)
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, {
    credentials: 'same-origin', cache: 'no-store', ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await createHttpError(response)
  return response
}

async function readPlan(response: Response, action: string): Promise<TestPlanResponse> {
  const payload: unknown = await response.json().catch(() => {
    throw new Error(`${action}未返回有效 JSON`)
  })
  const plan = parsePlan(payload)
  if (!plan) throw new Error(`${action}响应格式暂不支持`)
  return plan
}

export async function fetchPlans(projectId: string, signal?: AbortSignal): Promise<TestPlanResponse[]> {
  const response = await request(`/api/v1/test-plans?project_id=${encodeURIComponent(projectId)}`, { signal })
  const payload: unknown = await response.json().catch(() => { throw new Error('计划列表未返回有效 JSON') })
  if (!isRecord(payload) || !Array.isArray(payload.items) || !isInteger(payload.total, 0)) {
    throw new Error('计划列表响应格式暂不支持')
  }
  const plans = payload.items.map(parsePlan)
  if (plans.some((plan) => plan === null)) throw new Error('计划记录字段格式暂不支持')
  return plans.filter((plan): plan is TestPlanResponse => plan !== null)
}

export async function createPlan(input: TestPlanCreateInput, signal?: AbortSignal): Promise<TestPlanResponse> {
  const response = await request('/api/v1/test-plans', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
  return readPlan(response, '创建计划')
}

export async function updatePlan(
  planId: string, input: TestPlanUpdateInput, signal?: AbortSignal,
): Promise<TestPlanResponse> {
  const response = await request(`/api/v1/test-plans/${encodeURIComponent(planId)}`, {
    method: 'PUT', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
  return readPlan(response, '更新计划')
}

export async function executePlan(
  planId: string,
  options: PlanExecutionOptions = { execution_target: 'docker' },
): Promise<ExecutionBatch> {
  const response = await request(`/api/v1/test-plans/${encodeURIComponent(planId)}/executions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify(options),
  })
  const payload: unknown = await response.json().catch(() => { throw new Error('执行计划未返回有效 JSON') })
  const batch = parseBatch(payload)
  if (!batch) throw new Error('执行计划响应格式暂不支持')
  return batch
}

export function getTestPlanErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源测试计划服务'
  if (reason instanceof Error) return reason.message
  return '请求测试计划服务时发生未知错误'
}