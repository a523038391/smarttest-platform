import type {
  AdminSetupInput,
  AuthStatus,
  AuthUser,
  LoginCredentials,
} from '../types/auth'

type UnknownRecord = Record<string, unknown>

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseUser(value: unknown): AuthUser | null {
  if (!isRecord(value)
    || typeof value.id !== 'string' || value.id.length === 0
    || typeof value.username !== 'string' || value.username.length === 0
    || typeof value.display_name !== 'string' || value.display_name.length === 0
    || value.role !== 'ADMIN' || value.is_active !== true) return null

  return {
    id: value.id,
    username: value.username,
    display_name: value.display_name,
    role: value.role,
    is_active: value.is_active,
  }
}

function parseStatus(value: unknown): AuthStatus | null {
  if (!isRecord(value)
    || typeof value.setup_required !== 'boolean'
    || typeof value.auth_required !== 'boolean'
    || typeof value.authenticated !== 'boolean') return null

  const user = value.user === null ? null : parseUser(value.user)
  if (value.user !== null && user === null) return null
  if (value.setup_required && (value.authenticated || user !== null)) return null
  if (value.authenticated !== (user !== null)) return null

  return {
    setup_required: value.setup_required,
    auth_required: value.auth_required,
    authenticated: value.authenticated,
    user,
  }
}

const FIELD_LABELS: Record<string, string> = {
  username: '用户名',
  password: '密码',
  display_name: '显示名称',
}

const CODE_MESSAGES: Record<string, string> = {
  invalid_credentials: '用户名或密码不正确。',
  auth_already_initialized: '系统已完成初始化，请直接登录。',
  username_conflict: '该用户名已被使用，请更换一个用户名。',
  login_rate_limited: '登录尝试次数过多，请稍后再试。',
}

function translateFieldError(field: string, rawMessage: string, type: string): string {
  const label = FIELD_LABELS[field] ?? field
  if (type === 'missing') return `${label}不能为空`
  const tooShort = rawMessage.match(/at least (\d+) character/)
  if (tooShort) return `${label}长度不能少于 ${tooShort[1]} 位`
  const tooLong = rawMessage.match(/at most (\d+) character/)
  if (tooLong) return `${label}长度不能超过 ${tooLong[1]} 位`
  if (rawMessage.includes('unsafe characters')) {
    return `${label}只能包含字母、数字、点号、下划线或短横线`
  }
  if (rawMessage.includes('must not be blank')) return `${label}不能为空`
  const cleaned = rawMessage.replace(/^Value error,\s*/, '').trim()
  return cleaned ? `${label}：${cleaned}` : `${label}格式不正确`
}

function translateValidationErrors(errors: unknown): string | null {
  if (!Array.isArray(errors)) return null
  const messages: string[] = []
  for (const item of errors) {
    if (!isRecord(item)) continue
    const loc = Array.isArray(item.loc) ? item.loc : []
    const field = String(loc[loc.length - 1] ?? '')
    const rawMessage = typeof item.message === 'string' ? item.message : ''
    const type = typeof item.type === 'string' ? item.type : ''
    if (!field || !rawMessage) continue
    messages.push(translateFieldError(field, rawMessage, type))
  }
  return messages.length > 0 ? Array.from(new Set(messages)).join('；') : null
}

async function createHttpError(response: Response): Promise<Error> {
  const fallback = `认证服务返回 HTTP ${response.status}`
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase()
  if (mediaType !== 'application/problem+json') return new Error(fallback)

  try {
    const problem: unknown = await response.json()
    if (!isRecord(problem)) return new Error(fallback)
    const code = typeof problem.code === 'string'
      ? problem.code.trim().replace(/\s+/g, ' ').slice(0, 100) : ''

    if (code === 'validation_error') {
      const friendly = translateValidationErrors(problem.errors)
      if (friendly) return new Error(friendly)
    }
    if (code && CODE_MESSAGES[code]) return new Error(CODE_MESSAGES[code])

    const detail = typeof problem.detail === 'string'
      ? problem.detail.trim().replace(/\s+/g, ' ').slice(0, 500) : ''
    return new Error(detail || fallback)
  } catch {
    return new Error(fallback)
  }
}

async function requestStatus(
  path: string,
  method: 'GET' | 'POST',
  body?: LoginCredentials | AdminSetupInput,
  signal?: AbortSignal,
): Promise<AuthStatus> {
  const response = await fetch(path, {
    method,
    signal,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  })
  if (!response.ok) throw await createHttpError(response)

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new Error('认证服务未返回有效 JSON')
  }
  const status = parseStatus(payload)
  if (!status) throw new Error('认证服务响应格式暂不支持')
  return status
}

export function fetchAuthStatus(signal?: AbortSignal): Promise<AuthStatus> {
  return requestStatus('/api/v1/auth/status', 'GET', undefined, signal)
}

export function setupAdmin(input: AdminSetupInput, signal?: AbortSignal): Promise<AuthUser> {
  return authenticate('/api/v1/auth/setup', input, signal)
}

export function login(input: LoginCredentials, signal?: AbortSignal): Promise<AuthUser> {
  return authenticate('/api/v1/auth/login', input, signal)
}

async function authenticate(
  path: string,
  body: LoginCredentials | AdminSetupInput,
  signal?: AbortSignal,
): Promise<AuthUser> {
  const response = await fetch(path, {
    method: 'POST', signal, credentials: 'same-origin', cache: 'no-store',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw await createHttpError(response)
  let payload: unknown
  try { payload = await response.json() } catch { throw new Error('认证服务未返回有效 JSON') }
  const user = parseUser(payload)
  if (!user) throw new Error('认证服务响应格式暂不支持')
  return user
}

export async function logout(signal?: AbortSignal): Promise<void> {
  const response = await fetch('/api/v1/auth/logout', {
    method: 'POST',
    signal,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({}),
  })
  if (!response.ok) throw await createHttpError(response)
  if (response.status !== 204) throw new Error('退出登录响应格式暂不支持')
}

export function getAuthErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源认证服务'
  if (reason instanceof Error) return reason.message
  return '认证时发生未知错误'
}