import type {
  Connector, ConnectorAuthType, ConnectorCreateInput, ConnectorJson, ConnectorKind,
  ConnectorTestResult, ConnectorTool, ConnectorUpdateInput,
} from '../types/connectors'

type UnknownRecord = Record<string, unknown>
const kinds: readonly ConnectorKind[] = ['MCP', 'HTTP']
const authTypes: readonly ConnectorAuthType[] = ['NONE', 'BEARER', 'API_KEY']

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isJson(value: unknown): value is ConnectorJson {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isJson)
  return isRecord(value) && Object.values(value).every(isJson)
}

function isDateTime(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function parseTool(value: unknown): ConnectorTool | null {
  if (!isRecord(value) || typeof value.name !== 'string' || !value.name
    || typeof value.description !== 'string' || !isRecord(value.input_schema)
    || !isJson(value.input_schema)) return null
  return {
    name: value.name, description: value.description,
    input_schema: value.input_schema as Record<string, ConnectorJson>,
  }
}

function parseConnector(value: unknown): Connector | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !value.id
    || typeof value.name !== 'string' || !value.name
    || typeof value.description !== 'string' || !kinds.includes(value.kind as ConnectorKind)
    || typeof value.base_url !== 'string' || !authTypes.includes(value.auth_type as ConnectorAuthType)
    || typeof value.auth_header !== 'string' || typeof value.credential_configured !== 'boolean'
    || typeof value.enabled !== 'boolean' || typeof value.timeout_seconds !== 'number'
    || !Number.isFinite(value.timeout_seconds) || value.timeout_seconds <= 0
    || !Number.isInteger(value.state_version) || (value.state_version as number) < 0
    || !Array.isArray(value.tools) || !isDateTime(value.created_at) || !isDateTime(value.updated_at)
    || !(value.last_tested_at === null || isDateTime(value.last_tested_at))
    || !(value.last_test_success === null || typeof value.last_test_success === 'boolean')
    || !(value.last_error === null || typeof value.last_error === 'string')) return null
  const tools = value.tools.map(parseTool)
  if (tools.some((tool) => tool === null)) return null
  return {
    id: value.id, name: value.name, description: value.description,
    kind: value.kind as ConnectorKind, base_url: value.base_url,
    auth_type: value.auth_type as ConnectorAuthType, auth_header: value.auth_header,
    credential_configured: value.credential_configured, enabled: value.enabled,
    timeout_seconds: value.timeout_seconds, state_version: value.state_version as number,
    tools: tools as ConnectorTool[], last_tested_at: value.last_tested_at,
    last_test_success: value.last_test_success, last_error: value.last_error,
    created_at: value.created_at, updated_at: value.updated_at,
  }
}

async function httpError(response: Response): Promise<Error> {
  const payload: unknown = await response.json().catch(() => null)
  const code = isRecord(payload) && typeof payload.code === 'string' ? payload.code : ''
  const detail = isRecord(payload) && typeof payload.detail === 'string'
    ? payload.detail.trim().replace(/\s+/g, ' ').slice(0, 500) : ''
  const messages: Record<string, string> = {
    connector_version_conflict: '连接器已被其他操作修改，请重新加载后重试。',
    connector_name_conflict: '已存在同名连接器，请更换名称。',
    connector_credential_required: '当前鉴权方式需要凭据，请填写凭据后重试。',
    connector_conflict: '当前鉴权方式需要凭据，请填写凭据后重试。',
    connector_not_found: '连接器不存在或已被删除。',
    validation_error: '连接器配置校验失败，请检查填写内容。',
  }
  if (response.status === 401) return new Error('登录会话已失效，请重新登录。')
  const message = messages[code]
  if (message) return new Error(message)
  if (response.status === 404) return new Error('连接器不存在或已被删除。')
  if (response.status === 409) return new Error('连接器状态冲突，无法完成当前操作。')
  if (response.status === 422) return new Error('连接器配置校验失败，请检查填写内容。')
  return new Error(detail || `连接器服务请求失败（HTTP ${response.status}）。`)
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, {
    credentials: 'same-origin', cache: 'no-store', ...init,
    headers: { Accept: 'application/json, application/problem+json', ...(init?.headers ?? {}) },
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await httpError(response)
  return response
}

async function readConnector(response: Response, action: string): Promise<Connector> {
  const payload: unknown = await response.json().catch(() => { throw new Error(`${action}未返回有效 JSON`) })
  const connector = parseConnector(payload)
  if (!connector) throw new Error(`${action}响应格式暂不支持`)
  return connector
}

export async function fetchConnectors(signal?: AbortSignal): Promise<Connector[]> {
  const response = await request('/api/v1/connectors', { signal })
  const payload: unknown = await response.json().catch(() => {
    throw new Error('连接器列表未返回有效 JSON')
  })
  if (!isRecord(payload) || !Array.isArray(payload.items) || !Number.isInteger(payload.total)
    || (payload.total as number) < 0
    || payload.total !== payload.items.length) {
    throw new Error('连接器列表响应格式暂不支持')
  }
  const connectors = payload.items.map(parseConnector)
  if (connectors.some((item) => item === null)) throw new Error('连接器记录格式不安全')
  return connectors as Connector[]
}

export async function createConnector(input: ConnectorCreateInput): Promise<Connector> {
  return readConnector(await request('/api/v1/connectors', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  }), '创建连接器')
}

export async function updateConnector(id: string, input: ConnectorUpdateInput): Promise<Connector> {
  return readConnector(await request(`/api/v1/connectors/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  }), '保存连接器')
}

export async function deleteConnector(id: string): Promise<void> {
  await request(`/api/v1/connectors/${encodeURIComponent(id)}`, {
    method: 'DELETE', headers: { Accept: 'application/problem+json' },
  })
}

export async function setConnectorEnabled(
  id: string, enabled: boolean, stateVersion: number,
): Promise<Connector> {
  return readConnector(await request(`/api/v1/connectors/${encodeURIComponent(id)}/enabled`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, state_version: stateVersion }),
  }), enabled ? '启用连接器' : '停用连接器')
}

export async function testConnector(id: string): Promise<ConnectorTestResult> {
  const response = await request(`/api/v1/connectors/${encodeURIComponent(id)}/test`, { method: 'POST' })
  const payload: unknown = await response.json().catch(() => { throw new Error('连接测试未返回有效 JSON') })
  if (!isRecord(payload) || typeof payload.success !== 'boolean'
    || typeof payload.message !== 'string'
    || typeof payload.latency_ms !== 'number' || !Number.isFinite(payload.latency_ms) || payload.latency_ms < 0
    || !Number.isInteger(payload.tool_count) || (payload.tool_count as number) < 0) {
    throw new Error('连接测试响应格式暂不支持')
  }
  const connector = parseConnector(payload.connector)
  if (!connector) throw new Error('连接测试返回的连接器格式不安全')
  return {
    connector, success: payload.success, message: payload.message,
    latency_ms: payload.latency_ms, tool_count: payload.tool_count as number,
  }
}

export function getConnectorErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源连接器服务'
  if (reason instanceof Error) return reason.message
  return '请求连接器服务时发生未知错误'
}
