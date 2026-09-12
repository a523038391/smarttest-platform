import type { Engine } from '../types/platform'
import type {
  AutomationScript,
  AutomationScriptCreateInput,
  AutomationScriptStatus,
  AutomationScriptUpdateInput,
} from '../types/automation'

type UnknownRecord = Record<string, unknown>
const engines: readonly Engine[] = ['http', 'pytest', 'playwright']
const scriptStatuses: readonly AutomationScriptStatus[] = ['DRAFT', 'ACTIVE', 'ARCHIVED']
const digestPattern = /^[0-9a-f]{64}$/

export interface AutomationSourceUpload {
  source_ref: string
  content_digest: string
  size_bytes: number
}

function parseSourceUpload(payload: unknown): AutomationSourceUpload {
  if (!isRecord(payload) || typeof payload.source_ref !== 'string' || !payload.source_ref
    || typeof payload.content_digest !== 'string' || !digestPattern.test(payload.content_digest)
    || !Number.isInteger(payload.size_bytes) || (payload.size_bytes as number) < 0) {
    throw new Error('上传源码响应格式暂不支持')
  }
  return {
    source_ref: payload.source_ref,
    content_digest: payload.content_digest,
    size_bytes: payload.size_bytes as number,
  }
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isEngine(value: unknown): value is Engine {
  return typeof value === 'string' && engines.includes(value as Engine)
}

function isScriptStatus(value: unknown): value is AutomationScriptStatus {
  return typeof value === 'string' && scriptStatuses.includes(value as AutomationScriptStatus)
}

function isDateTime(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function parseScript(value: unknown): AutomationScript | null {
  if (!isRecord(value) || typeof value.id !== 'string' || value.id.length === 0
    || typeof value.project_id !== 'string' || typeof value.name !== 'string'
    || typeof value.description !== 'string' || !isEngine(value.engine)
    || typeof value.entrypoint !== 'string' || typeof value.source_ref !== 'string'
    || typeof value.content_digest !== 'string' || !Number.isInteger(value.timeout_seconds)
    || !isScriptStatus(value.status) || !Number.isInteger(value.revision)
    || !Number.isInteger(value.state_version) || (value.state_version as number) < 0
    || !isDateTime(value.created_at) || !isDateTime(value.updated_at)) return null
  return {
    id: value.id,
    project_id: value.project_id,
    name: value.name,
    description: value.description,
    engine: value.engine,
    entrypoint: value.entrypoint,
    source_ref: value.source_ref,
    content_digest: value.content_digest,
    timeout_seconds: value.timeout_seconds as number,
    status: value.status,
    revision: value.revision as number,
    state_version: value.state_version as number,
    created_at: value.created_at,
    updated_at: value.updated_at,
  }
}

async function createHttpError(response: Response): Promise<Error> {
  const fallback = `自动化服务返回 HTTP ${response.status}`
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase()
  if (mediaType !== 'application/problem+json') return new Error(fallback)

  try {
    const problem: unknown = await response.json()
    if (!isRecord(problem)) return new Error(fallback)
    const code = typeof problem.code === 'string' ? problem.code : ''
    if (code === 'asset_version_conflict') {
      return new Error('该脚本已被其他操作修改，请刷新后重试。')
    }
    if (code === 'automation_script_not_found') return new Error('未找到该自动化脚本。')
    if (code === 'automation_source_invalid') {
      const detail = typeof problem.detail === 'string' ? problem.detail : ''
      if (detail.includes('host execution is disabled')) return new Error('Windows 本机执行尚未启用，请联系管理员配置白名单。')
      if (detail.includes('outside the allowed roots')) return new Error('本机项目目录不在管理员配置的白名单中。')
      if (detail.includes('Python executable')) return new Error('Python 解释器必须存在且位于本机项目目录内。')
      if (detail.includes('host project directory')) return new Error('本机项目目录无效，请填写已存在的绝对路径。')
      if (detail.includes('active project')) return new Error('只有启用中的项目可以使用 Windows 本机执行。')
      if (detail.includes('10 MiB')) return new Error('项目包超过 10 MiB，请删除无关文件后重试。')
      if (detail.includes('50 MiB')) return new Error('项目解压后超过 50 MiB，请精简项目后重试。')
      if (detail.includes('entrypoint')) return new Error('入口文件在项目中不存在，请检查相对路径。')
      if (detail.includes('unsafe path') || detail.includes('links')) return new Error('ZIP 包含不安全的路径或链接，请重新打包。')
      if (detail.includes('Git URL')) return new Error('Git 地址必须是受支持平台上的公开 HTTPS 仓库。')
      if (detail.includes('import failed')) return new Error('无法访问 Git 仓库，请确认地址正确、仓库为公开状态且分支存在。私有仓库请改用 ZIP 上传。')
      if (detail.includes('reference')) return new Error('Git 分支、标签或提交无效。')
      return new Error('源码项目校验失败，请检查 ZIP 或 Git 仓库内容。')
    }
    if (code === 'validation_error' && Array.isArray(problem.errors)) {
      const messages = problem.errors
        .filter(isRecord)
        .map((item) => (typeof item.message === 'string' ? item.message : ''))
        .filter((item) => item.length > 0)
      if (messages.length > 0) return new Error(`脚本信息校验失败：${messages.join('；')}`)
    }
    const detail = typeof problem.detail === 'string'
      ? problem.detail.trim().replace(/\s+/g, ' ').slice(0, 500) : ''
    return new Error(detail || fallback)
  } catch {
    return new Error(fallback)
  }
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

export async function fetchScripts(
  projectId: string,
  signal?: AbortSignal,
): Promise<AutomationScript[]> {
  const response = await request(
    `/api/v1/automation-scripts?project_id=${encodeURIComponent(projectId)}`,
    { signal },
  )
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new Error('脚本列表未返回有效 JSON')
  }
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new Error('脚本列表响应格式暂不支持')
  }
  const scripts = payload.items.map(parseScript)
  if (scripts.some((item) => item === null)) throw new Error('脚本记录字段格式暂不支持')
  return scripts.filter((item): item is AutomationScript => item !== null)
}

export async function uploadAutomationSource(
  projectId: string,
  content: string,
  signal?: AbortSignal,
): Promise<AutomationSourceUpload> {
  const response = await request('/api/v1/automation-sources', {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: projectId, content }),
  })
  const payload: unknown = await response.json().catch(() => {
    throw new Error('上传源码未返回有效 JSON')
  })
  return parseSourceUpload(payload)
}

export async function uploadAutomationZip(
  projectId: string,
  archive: File,
  signal?: AbortSignal,
): Promise<AutomationSourceUpload> {
  const response = await request(
    `/api/v1/automation-sources/zip?project_id=${encodeURIComponent(projectId)}`,
    {
      method: 'POST', signal, headers: { 'Content-Type': 'application/zip' }, body: archive,
    },
  )
  const payload: unknown = await response.json().catch(() => {
    throw new Error('上传 ZIP 未返回有效 JSON')
  })
  return parseSourceUpload(payload)
}

export async function importAutomationGit(
  projectId: string,
  repositoryUrl: string,
  gitRef: string,
  signal?: AbortSignal,
): Promise<AutomationSourceUpload> {
  const response = await request('/api/v1/automation-sources/git', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId, repository_url: repositoryUrl, git_ref: gitRef || 'HEAD',
    }),
  })
  const payload: unknown = await response.json().catch(() => {
    throw new Error('导入 Git 仓库未返回有效 JSON')
  })
  return parseSourceUpload(payload)
}

export async function registerAutomationHostSource(
  projectId: string,
  projectDirectory: string,
  pythonExecutable: string,
  signal?: AbortSignal,
): Promise<AutomationSourceUpload> {
  const response = await request('/api/v1/automation-sources/host', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId,
      project_directory: projectDirectory,
      python_executable: pythonExecutable,
    }),
  })
  const payload: unknown = await response.json().catch(() => {
    throw new Error('注册本机项目未返回有效 JSON')
  })
  return parseSourceUpload(payload)
}

export async function createScript(
  input: AutomationScriptCreateInput,
  signal?: AbortSignal,
): Promise<AutomationScript> {
  const response = await request('/api/v1/automation-scripts', {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  const payload: unknown = await response.json().catch(() => { throw new Error('创建脚本未返回有效 JSON') })
  const script = parseScript(payload)
  if (!script) throw new Error('创建脚本响应格式暂不支持')
  return script
}

export async function updateScript(
  scriptId: string,
  input: AutomationScriptUpdateInput,
  signal?: AbortSignal,
): Promise<AutomationScript> {
  const response = await request(`/api/v1/automation-scripts/${encodeURIComponent(scriptId)}`, {
    method: 'PUT',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  const payload: unknown = await response.json().catch(() => { throw new Error('更新脚本未返回有效 JSON') })
  const script = parseScript(payload)
  if (!script) throw new Error('更新脚本响应格式暂不支持')
  return script
}

export function getAutomationErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源自动化服务'
  if (reason instanceof Error) return reason.message
  return '请求自动化服务时发生未知错误'
}
