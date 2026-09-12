import type { Project, ProjectCreateInput, ProjectStatus } from '../types/project'

type UnknownRecord = Record<string, unknown>
const projectStatuses: readonly ProjectStatus[] = ['ACTIVE', 'ARCHIVED']

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isProjectStatus(value: unknown): value is ProjectStatus {
  return typeof value === 'string' && projectStatuses.includes(value as ProjectStatus)
}

function isDateTime(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function parseProject(value: unknown): Project | null {
  if (!isRecord(value) || typeof value.id !== 'string' || value.id.length === 0
    || typeof value.name !== 'string' || value.name.length === 0
    || typeof value.description !== 'string' || !isProjectStatus(value.status)
    || !Number.isInteger(value.state_version) || (value.state_version as number) < 0
    || !isDateTime(value.created_at) || !isDateTime(value.updated_at)) return null
  return {
    id: value.id,
    name: value.name,
    description: value.description,
    status: value.status,
    state_version: value.state_version as number,
    created_at: value.created_at,
    updated_at: value.updated_at,
  }
}

async function createHttpError(response: Response): Promise<Error> {
  const fallback = `项目服务返回 HTTP ${response.status}`
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase()
  if (mediaType !== 'application/problem+json') return new Error(fallback)

  try {
    const problem: unknown = await response.json()
    if (!isRecord(problem)) return new Error(fallback)
    const code = typeof problem.code === 'string' ? problem.code : ''
    if (code === 'project_name_conflict') return new Error('已存在同名项目，请更换一个名称。')
    if (code === 'project_in_use') {
      return new Error('该项目中仍有需求、用例、脚本、环境或测试计划，请先处理这些内容。')
    }
    if (code === 'project_not_found') return new Error('项目不存在或已被删除。')
    const detail = typeof problem.detail === 'string'
      ? problem.detail.trim().replace(/\s+/g, ' ').slice(0, 500) : ''
    return new Error(detail || fallback)
  } catch {
    return new Error(fallback)
  }
}

export async function fetchProjects(
  status?: ProjectStatus,
  signal?: AbortSignal,
): Promise<Project[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  const response = await fetch(`/api/v1/projects${query}`, {
    signal,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await createHttpError(response)

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new Error('项目列表未返回有效 JSON')
  }
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new Error('项目列表响应格式暂不支持')
  }
  const projects = payload.items.map(parseProject)
  if (projects.some((item) => item === null)) throw new Error('项目记录字段格式暂不支持')
  return projects.filter((item): item is Project => item !== null)
}

export async function createProject(
  input: ProjectCreateInput,
  signal?: AbortSignal,
): Promise<Project> {
  const response = await fetch('/api/v1/projects', {
    method: 'POST',
    signal,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await createHttpError(response)

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new Error('创建项目未返回有效 JSON')
  }
  const project = parseProject(payload)
  if (!project) throw new Error('创建项目响应格式暂不支持')
  return project
}

export async function deleteProject(projectId: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE',
    signal,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/problem+json' },
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await createHttpError(response)
}

export function getProjectErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源项目服务'
  if (reason instanceof Error) return reason.message
  return '请求项目服务时发生未知错误'
}
