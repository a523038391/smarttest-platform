import type {
  DataFactoryWorkflow,
  JsonObject,
  JsonValue,
  WorkflowCreateInput,
  WorkflowDebugInput,
  WorkflowEdge,
  WorkflowEdgeOutcome,
  WorkflowExecutionInput,
  WorkflowNode,
  WorkflowNodeType,
  WorkflowRun,
  WorkflowUpdateInput,
} from '../types/data-factory'

type UnknownRecord = Record<string, unknown>
const nodeTypes: readonly WorkflowNodeType[] = ['http', 'condition', 'parallel']
const outcomes: readonly WorkflowEdgeOutcome[] = ['always', 'success', 'failure', 'true', 'false']

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

function parseNode(value: unknown): WorkflowNode | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.name !== 'string' || !nodeTypes.includes(value.type as WorkflowNodeType)
    || typeof value.x !== 'number' || !Number.isFinite(value.x)
    || typeof value.y !== 'number' || !Number.isFinite(value.y)
    || !isJsonObject(value.config)) return null
  return { id: value.id, name: value.name, type: value.type as WorkflowNodeType,
    x: value.x, y: value.y, config: value.config }
}

function parseEdge(value: unknown): WorkflowEdge | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.source !== 'string' || !value.source
    || typeof value.target !== 'string' || !value.target
    || !outcomes.includes(value.outcome as WorkflowEdgeOutcome)) return null
  return { id: value.id, source: value.source, target: value.target,
    outcome: value.outcome as WorkflowEdgeOutcome }
}

function parseWorkflow(value: unknown): DataFactoryWorkflow | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.project_id !== 'string' || !value.project_id
    || typeof value.name !== 'string' || typeof value.description !== 'string'
    || !Array.isArray(value.nodes) || !Array.isArray(value.edges)
    || !isJsonObject(value.variables) || !Number.isInteger(value.state_version)
    || (value.state_version as number) < 0 || !isDateTime(value.created_at)
    || !isDateTime(value.updated_at)) return null
  const nodes = value.nodes.map(parseNode)
  const edges = value.edges.map(parseEdge)
  if (nodes.some((item) => item === null) || edges.some((item) => item === null)) return null
  return { id: value.id, project_id: value.project_id, name: value.name,
    description: value.description, nodes: nodes as WorkflowNode[], edges: edges as WorkflowEdge[],
    variables: value.variables, state_version: value.state_version as number,
    created_at: value.created_at, updated_at: value.updated_at }
}

function parseRun(value: unknown): WorkflowRun | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.workflow_id !== 'string' || !value.workflow_id
    || (value.status !== 'SUCCEEDED' && value.status !== 'FAILED')
    || !isJsonObject(value.node_results) || !isJsonObject(value.variables)
    || !isDateTime(value.started_at)
    || !(value.finished_at === null || isDateTime(value.finished_at))) return null
  return { id: value.id, workflow_id: value.workflow_id, status: value.status,
    node_results: value.node_results, variables: value.variables,
    started_at: value.started_at, finished_at: value.finished_at }
}

async function httpError(response: Response): Promise<Error> {
  const payload: unknown = await response.json().catch(() => null)
  const code = isRecord(payload) && typeof payload.code === 'string' ? payload.code : ''
  if (response.status === 401) return new Error('登录会话已失效，请重新登录。')
  if (response.status === 404) return new Error('未找到该工作流，它可能已被删除。')
  if (code === 'workflow_name_conflict') return new Error('当前项目中已存在同名工作流。')
  if (code === 'workflow_version_conflict') return new Error('工作流已被其他操作修改，请刷新后重试。')
  if (response.status === 409) return new Error('当前工作流状态不允许此操作。')
  if (response.status === 422 || code === 'validation_error') return new Error('工作流配置校验失败，请检查节点、连线和变量。')
  const detail = isRecord(payload) && typeof payload.detail === 'string' ? payload.detail.trim() : ''
  return new Error(detail || `数据工厂服务请求失败（HTTP ${response.status}）。`)
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) } })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await httpError(response)
  return response
}

async function readWorkflow(response: Response, action: string): Promise<DataFactoryWorkflow> {
  const payload: unknown = await response.json().catch(() => { throw new Error(`${action}未返回有效 JSON`) })
  const workflow = parseWorkflow(payload)
  if (!workflow) throw new Error(`${action}响应格式暂不支持`)
  return workflow
}

async function readRun(response: Response, action: string): Promise<WorkflowRun> {
  const payload: unknown = await response.json().catch(() => { throw new Error(`${action}未返回有效 JSON`) })
  const run = parseRun(payload)
  if (!run) throw new Error(`${action}响应格式暂不支持`)
  return run
}

function readItems(payload: unknown): unknown[] | null {
  if (Array.isArray(payload)) return payload
  return isRecord(payload) && Array.isArray(payload.items) ? payload.items : null
}

export async function fetchWorkflows(projectId: string, signal?: AbortSignal): Promise<DataFactoryWorkflow[]> {
  const response = await request(`/api/v1/data-factory/workflows?project_id=${encodeURIComponent(projectId)}`, { signal })
  const payload: unknown = await response.json().catch(() => { throw new Error('工作流列表未返回有效 JSON') })
  const items = readItems(payload)
  if (!items) throw new Error('工作流列表响应格式暂不支持')
  const workflows = items.map(parseWorkflow)
  if (workflows.some((item) => item === null)) throw new Error('工作流记录字段格式暂不支持')
  return workflows as DataFactoryWorkflow[]
}

export async function createWorkflow(input: WorkflowCreateInput): Promise<DataFactoryWorkflow> {
  const response = await request('/api/v1/data-factory/workflows', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  return readWorkflow(response, '创建工作流')
}

export async function updateWorkflow(id: string, input: WorkflowUpdateInput): Promise<DataFactoryWorkflow> {
  const response = await request(`/api/v1/data-factory/workflows/${encodeURIComponent(id)}`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  return readWorkflow(response, '保存工作流')
}

export async function deleteWorkflow(id: string): Promise<void> {
  await request(`/api/v1/data-factory/workflows/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function executeWorkflow(id: string, input: WorkflowExecutionInput): Promise<WorkflowRun> {
  const response = await request(`/api/v1/data-factory/workflows/${encodeURIComponent(id)}/execute`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  return readRun(response, '执行工作流')
}

export async function debugWorkflow(id: string, input: WorkflowDebugInput): Promise<WorkflowRun> {
  const response = await request(`/api/v1/data-factory/workflows/${encodeURIComponent(id)}/debug`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  return readRun(response, '调试节点')
}

export async function fetchWorkflowRuns(id: string, signal?: AbortSignal): Promise<WorkflowRun[]> {
  const response = await request(`/api/v1/data-factory/workflows/${encodeURIComponent(id)}/runs`, { signal })
  const payload: unknown = await response.json().catch(() => { throw new Error('执行记录未返回有效 JSON') })
  const items = readItems(payload)
  if (!items) throw new Error('执行记录响应格式暂不支持')
  const runs = items.map(parseRun)
  if (runs.some((item) => item === null)) throw new Error('执行记录字段格式暂不支持')
  return runs as WorkflowRun[]
}

export function getDataFactoryErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源数据工厂服务'
  if (reason instanceof Error) return reason.message
  return '请求数据工厂服务时发生未知错误'
}