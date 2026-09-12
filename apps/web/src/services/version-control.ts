import type {
  VersionControlPublishInput,
  VersionControlStatus,
} from '../types/version-control'

type UnknownRecord = Record<string, unknown>

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function optionalString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function optionalBoolean(value: unknown): value is boolean | null {
  return value === null || typeof value === 'boolean'
}

function parseStatus(value: unknown): VersionControlStatus | null {
  if (!isRecord(value)
    || typeof value.enabled !== 'boolean'
    || typeof value.repository_present !== 'boolean'
    || !optionalString(value.branch)
    || !optionalString(value.head_short)
    || !optionalBoolean(value.clean)
    || !Number.isInteger(value.change_count)
    || (value.change_count as number) < 0
    || !Array.isArray(value.changed_paths)
    || !value.changed_paths.every((path) => typeof path === 'string')
    || typeof value.remote_configured !== 'boolean') return null

  return {
    enabled: value.enabled,
    repository_present: value.repository_present,
    branch: value.branch,
    head_short: value.head_short,
    clean: value.clean,
    change_count: value.change_count as number,
    changed_paths: value.changed_paths,
    remote_configured: value.remote_configured,
  }
}

const CODE_MESSAGES: Record<string, string> = {
  version_control_disabled: '代码版本功能尚未启用，请联系管理员完成服务端配置。',
  version_control_forbidden: '仅管理员可以管理代码版本。',
  repository_unavailable: '当前服务目录不是代码仓库，请联系管理员检查部署配置。',
  remote_unavailable: '代码仓库尚未配置远程仓库，请联系管理员处理。',
  dirty_working_tree: '工作区存在未提交修改，请先上传代码后再拉取。',
  detached_head: '当前仓库未处于有效分支，无法执行此操作。',
  sensitive_files_staged: '检测到敏感配置、密钥、数据库或备份文件，已停止上传并撤销暂存。',
  git_operation_timed_out: 'Git 操作超时，请检查网络后重试。',
  git_operation_failed: 'Git 操作失败，请检查远程仓库权限、网络和本机凭据。',
}

async function createHttpError(response: Response): Promise<Error> {
  const fallback = `代码版本服务返回 HTTP ${response.status}`
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase()
  if (mediaType !== 'application/problem+json') return new Error(fallback)

  try {
    const problem: unknown = await response.json()
    if (!isRecord(problem)) return new Error(fallback)
    const code = typeof problem.code === 'string' ? problem.code.trim().slice(0, 100) : ''
    if (code && CODE_MESSAGES[code]) return new Error(CODE_MESSAGES[code])
    const detail = typeof problem.detail === 'string'
      ? problem.detail.trim().replace(/\s+/g, ' ').slice(0, 500) : ''
    return new Error(detail || fallback)
  } catch {
    return new Error(fallback)
  }
}

async function requestStatus(path: string, init?: RequestInit): Promise<VersionControlStatus> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await createHttpError(response)

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new Error('代码版本服务未返回有效 JSON')
  }
  const status = parseStatus(payload)
  if (!status) throw new Error('代码版本服务响应格式暂不支持')
  return status
}

export function fetchVersionControlStatus(signal?: AbortSignal): Promise<VersionControlStatus> {
  return requestStatus('/api/v1/version-control/status', { signal })
}

export function pullVersionControl(signal?: AbortSignal): Promise<VersionControlStatus> {
  return requestStatus('/api/v1/version-control/pull', { method: 'POST', signal })
}

export function publishVersionControl(
  input: VersionControlPublishInput,
  signal?: AbortSignal,
): Promise<VersionControlStatus> {
  return requestStatus('/api/v1/version-control/publish', {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export function getVersionControlErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源代码版本服务'
  if (reason instanceof Error) return reason.message
  return '请求代码版本服务时发生未知错误'
}