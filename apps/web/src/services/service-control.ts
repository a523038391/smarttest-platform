import type {
  ServiceControlStatus,
  ServiceRestartResponse,
  ServiceRestartTarget,
} from '../types/service-control'

type UnknownRecord = Record<string, unknown>

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseStatus(value: unknown): ServiceControlStatus | null {
  if (!isRecord(value)
    || typeof value.enabled !== 'boolean'
    || typeof value.supervisor_online !== 'boolean'
    || typeof value.auto_restart_enabled !== 'boolean') return null
  return {
    enabled: value.enabled,
    supervisor_online: value.supervisor_online,
    auto_restart_enabled: value.auto_restart_enabled,
  }
}

function isRestartTarget(value: unknown): value is ServiceRestartTarget {
  return value === 'frontend' || value === 'backend' || value === 'all'
}

function parseRestart(value: unknown): ServiceRestartResponse | null {
  if (!isRecord(value)
    || value.accepted !== true
    || !isRestartTarget(value.target)
    || typeof value.reconnect_after_seconds !== 'number'
    || !Number.isFinite(value.reconnect_after_seconds)
    || value.reconnect_after_seconds < 0) return null
  return {
    accepted: true,
    target: value.target,
    reconnect_after_seconds: value.reconnect_after_seconds,
  }
}

const CODE_MESSAGES: Record<string, string> = {
  service_control_forbidden: '仅管理员可以控制平台服务。',
  service_control_unavailable: '服务守护进程当前不可用，请联系管理员检查守护进程状态。',
}

async function createHttpError(response: Response): Promise<Error> {
  const fallback = `服务控制接口返回 HTTP ${response.status}`
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase()
  if (mediaType !== 'application/problem+json') return new Error(fallback)
  const problem: unknown = await response.json().catch(() => null)
  if (!isRecord(problem)) return new Error(fallback)
  const code = typeof problem.code === 'string' ? problem.code.trim().slice(0, 100) : ''
  if (code && CODE_MESSAGES[code]) return new Error(CODE_MESSAGES[code])
  const detail = typeof problem.detail === 'string'
    ? problem.detail.trim().replace(/\s+/g, ' ').slice(0, 500) : ''
  return new Error(detail || fallback)
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await createHttpError(response)
  return response
}

async function readJson(response: Response, action: string): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    throw new Error(`${action}未返回有效 JSON`)
  }
}

export async function fetchServiceControlStatus(
  signal?: AbortSignal,
): Promise<ServiceControlStatus> {
  const response = await request('/api/v1/service-control/status', { signal })
  const status = parseStatus(await readJson(response, '服务控制状态接口'))
  if (!status) throw new Error('服务控制状态响应格式暂不支持')
  return status
}

export async function restartService(
  target: ServiceRestartTarget,
  signal?: AbortSignal,
): Promise<ServiceRestartResponse> {
  const response = await request('/api/v1/service-control/restart', {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target }),
  })
  if (response.status !== 202) throw new Error('服务重启接口未返回预期的接受状态')
  const result = parseRestart(await readJson(response, '服务重启接口'))
  if (!result || result.target !== target) throw new Error('服务重启响应格式暂不支持')
  return result
}

export function getServiceControlErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源服务控制接口'
  if (reason instanceof Error) return reason.message
  return '请求服务控制接口时发生未知错误'
}