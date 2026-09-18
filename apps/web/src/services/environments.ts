import type {
  ConfigurationJson, ConfigurationValue, Environment, EnvironmentCreateInput,
  EnvironmentStatus, EnvironmentUpdateInput,
} from '../types/environment'

type UnknownRecord = Record<string, unknown>
const statuses: readonly EnvironmentStatus[] = ['DRAFT', 'ACTIVE', 'ARCHIVED']
const sensitiveNames = new Set(['AUTH_TOKEN', 'AUTH_ACCOUNT', 'AUTH_PASSWORD'])
const valueKeys = ['name', 'secret', 'value'] as const
const secretKeys = ['name', 'secret', 'configured', 'secret_ref'] as const
const environmentKeys = [
  'id', 'project_id', 'name', 'status', 'revision', 'state_version',
  'environment_variables', 'common_parameters', 'created_at', 'updated_at',
] as const

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasOnlyKeys(value: UnknownRecord, allowed: readonly string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key))
}

function isJson(value: unknown): value is ConfigurationJson {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isJson)
  return isRecord(value) && Object.values(value).every(isJson)
}

function isDateTime(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function isUuid(value: unknown): value is string {
  return typeof value === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

function parseValue(value: unknown, requireString: boolean): ConfigurationValue | null {
  if (!isRecord(value) || typeof value.name !== 'string' || !value.name) return null
  if (value.secret === true) {
    if (!hasOnlyKeys(value, secretKeys) || value.configured !== true
      || !isUuid(value.secret_ref) || 'value' in value) return null
    return { name: value.name, secret: true, configured: true, secret_ref: value.secret_ref }
  }
  if (value.secret !== false || !hasOnlyKeys(value, valueKeys) || !('value' in value)
    || sensitiveNames.has(value.name) || !isJson(value.value)
    || (requireString && typeof value.value !== 'string')
    || 'configured' in value || 'secret_ref' in value) return null
  return { name: value.name, secret: false, value: value.value }
}

function parseEnvironment(value: unknown): Environment | null {
  if (!isRecord(value) || !hasOnlyKeys(value, environmentKeys)
    || !isUuid(value.id) || !isUuid(value.project_id)
    || typeof value.name !== 'string' || !value.name || !statuses.includes(value.status as EnvironmentStatus)
    || !Number.isInteger(value.revision) || (value.revision as number) < 1
    || !Number.isInteger(value.state_version) || (value.state_version as number) < 0
    || !Array.isArray(value.environment_variables) || !Array.isArray(value.common_parameters)
    || !isDateTime(value.created_at) || !isDateTime(value.updated_at)) return null
  const environmentVariables = value.environment_variables.map((item) => parseValue(item, true))
  const commonParameters = value.common_parameters.map((item) => parseValue(item, false))
  if (environmentVariables.some((item) => item === null) || commonParameters.some((item) => item === null)) return null
  return {
    id: value.id, project_id: value.project_id, name: value.name,
    status: value.status as EnvironmentStatus, revision: value.revision as number,
    state_version: value.state_version as number,
    environment_variables: environmentVariables as ConfigurationValue[],
    common_parameters: commonParameters as ConfigurationValue[],
    created_at: value.created_at, updated_at: value.updated_at,
  }
}

async function httpError(response: Response): Promise<Error> {
  const payload: unknown = await response.json().catch(() => null)
  const code = isRecord(payload) && typeof payload.code === 'string' ? payload.code : ''
  if (response.status === 401) return new Error('登录会话已失效，请重新登录。')
  if (response.status === 404) return new Error('环境不存在或已被删除。')
  if (code === 'environment_version_conflict') return new Error('环境已被其他操作修改，请刷新后重试。')
  if (code === 'environment_conflict') return new Error('环境名称重复，或敏感配置引用已失效。')
  if (code === 'secret_encryption_unavailable') return new Error('平台尚未启用敏感配置加密，暂时无法保存 Token 或密码。')
  if (response.status === 422) return new Error('环境配置校验失败，请检查填写内容。')
  return new Error(`环境服务请求失败（HTTP ${response.status}）。`)
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) } })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await httpError(response)
  return response
}

async function readEnvironment(response: Response, action: string): Promise<Environment> {
  const payload: unknown = await response.json().catch(() => { throw new Error(`${action}未返回有效 JSON`) })
  const environment = parseEnvironment(payload)
  if (!environment) throw new Error(`${action}响应格式或敏感字段格式不安全`)
  return environment
}

export async function fetchEnvironments(projectId: string, signal?: AbortSignal): Promise<Environment[]> {
  const response = await request(`/api/v1/environments?project_id=${encodeURIComponent(projectId)}`, { signal })
  const payload: unknown = await response.json().catch(() => { throw new Error('环境列表未返回有效 JSON') })
  if (!isRecord(payload) || !hasOnlyKeys(payload, ['items', 'total']) || !Array.isArray(payload.items)
    || !Number.isInteger(payload.total) || payload.total !== payload.items.length) {
    throw new Error('环境列表响应格式暂不支持')
  }
  const environments = payload.items.map(parseEnvironment)
  if (environments.some((item) => item === null)) throw new Error('环境记录格式或敏感字段格式不安全')
  return environments as Environment[]
}

export async function createEnvironment(input: EnvironmentCreateInput): Promise<Environment> {
  return readEnvironment(await request('/api/v1/environments', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) }), '创建环境')
}

export async function updateEnvironment(id: string, input: EnvironmentUpdateInput): Promise<Environment> {
  return readEnvironment(await request(`/api/v1/environments/${encodeURIComponent(id)}`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) }), '保存环境')
}

export function getEnvironmentErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源环境服务'
  if (reason instanceof Error) return reason.message
  return '请求环境服务时发生未知错误'
}