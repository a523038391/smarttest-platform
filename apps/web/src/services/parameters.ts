import type {
  JsonScalar,
  ParameterEnumCreateInput,
  ParameterEnumOption,
  ParameterEnumSet,
  ParameterEnumUpdateInput,
  ParameterInputType,
  ScriptParameterDefinition,
} from '../types/parameters'

type UnknownRecord = Record<string, unknown>
const inputTypes: readonly ParameterInputType[] = [
  'text', 'number', 'integer', 'boolean', 'enum',
]

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isScalar(value: unknown): value is JsonScalar {
  return value === null || typeof value === 'string' || typeof value === 'boolean'
    || (typeof value === 'number' && Number.isFinite(value))
}

function parseOption(value: unknown): ParameterEnumOption | null {
  if (!isRecord(value) || typeof value.label !== 'string' || !isScalar(value.value)) return null
  return { label: value.label, value: value.value }
}

function parseEnumSet(value: unknown): ParameterEnumSet | null {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.project_id !== 'string'
    || typeof value.name !== 'string' || typeof value.description !== 'string'
    || !Array.isArray(value.options) || !Number.isInteger(value.state_version)
    || typeof value.created_at !== 'string' || typeof value.updated_at !== 'string') return null
  const options = value.options.map(parseOption)
  if (options.some((option) => option === null)) return null
  return {
    id: value.id, project_id: value.project_id, name: value.name,
    description: value.description,
    options: options.filter((option): option is ParameterEnumOption => option !== null),
    state_version: value.state_version as number,
    created_at: value.created_at, updated_at: value.updated_at,
  }
}

function parseDefinition(value: unknown): ScriptParameterDefinition | null {
  if (!isRecord(value) || typeof value.name !== 'string' || typeof value.label !== 'string'
    || typeof value.input_type !== 'string'
    || !inputTypes.includes(value.input_type as ParameterInputType)
    || typeof value.required !== 'boolean'
    || !(value.enum_set_id === null || typeof value.enum_set_id === 'string')
    || !isScalar(value.default_value) || !Number.isInteger(value.position)) return null
  return {
    name: value.name, label: value.label,
    input_type: value.input_type as ParameterInputType,
    required: value.required, enum_set_id: value.enum_set_id,
    default_value: value.default_value, position: value.position as number,
  }
}

async function httpError(response: Response): Promise<Error> {
  let code = ''
  if (response.headers.get('content-type')?.includes('application/problem+json')) {
    const payload: unknown = await response.json().catch(() => null)
    if (isRecord(payload) && typeof payload.code === 'string') code = payload.code
  }
  const messages: Record<string, string> = {
    parameter_enum_not_found: '未找到该枚举。',
    parameter_enum_version_conflict: '枚举已被其他操作修改，请刷新后重试。',
    parameter_enum_conflict: '枚举名称重复，或仍被脚本入参引用。',
    script_parameter_conflict: '脚本入参配置无效，请检查枚举和默认值。',
    automation_script_not_found: '未找到该自动化脚本。',
    validation_error: '参数配置校验失败，请检查填写内容。',
  }
  return new Error(messages[code] ?? `参数配置服务请求失败（HTTP ${response.status}）。`)
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, {
    credentials: 'same-origin', cache: 'no-store', ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await httpError(response)
  return response
}

export async function fetchParameterEnums(
  projectId: string, signal?: AbortSignal,
): Promise<ParameterEnumSet[]> {
  const response = await request(
    `/api/v1/parameter-enums?project_id=${encodeURIComponent(projectId)}`, { signal },
  )
  const payload: unknown = await response.json().catch(() => null)
  if (!isRecord(payload) || !Array.isArray(payload.items)) throw new Error('枚举列表响应格式暂不支持。')
  const items = payload.items.map(parseEnumSet)
  if (items.some((item) => item === null)) throw new Error('枚举数据格式暂不支持。')
  return items.filter((item): item is ParameterEnumSet => item !== null)
}

async function readEnum(response: Response): Promise<ParameterEnumSet> {
  const item = parseEnumSet(await response.json().catch(() => null))
  if (!item) throw new Error('枚举响应格式暂不支持。')
  return item
}

export async function createParameterEnum(
  input: ParameterEnumCreateInput,
): Promise<ParameterEnumSet> {
  return readEnum(await request('/api/v1/parameter-enums', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }))
}

export async function updateParameterEnum(
  id: string, input: ParameterEnumUpdateInput,
): Promise<ParameterEnumSet> {
  return readEnum(await request(`/api/v1/parameter-enums/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }))
}

export async function deleteParameterEnum(id: string): Promise<void> {
  await request(`/api/v1/parameter-enums/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function fetchScriptParameters(
  scriptId: string, signal?: AbortSignal,
): Promise<ScriptParameterDefinition[]> {
  const response = await request(
    `/api/v1/automation-scripts/${encodeURIComponent(scriptId)}/parameters`, { signal },
  )
  const payload: unknown = await response.json().catch(() => null)
  if (!Array.isArray(payload)) throw new Error('脚本入参响应格式暂不支持。')
  const items = payload.map(parseDefinition)
  if (items.some((item) => item === null)) throw new Error('脚本入参数据格式暂不支持。')
  return items.filter((item): item is ScriptParameterDefinition => item !== null)
}

export async function replaceScriptParameters(
  scriptId: string, items: ScriptParameterDefinition[],
): Promise<ScriptParameterDefinition[]> {
  const response = await request(
    `/api/v1/automation-scripts/${encodeURIComponent(scriptId)}/parameters`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(items),
    },
  )
  const payload: unknown = await response.json().catch(() => null)
  if (!Array.isArray(payload)) throw new Error('保存脚本入参未返回有效数据。')
  const parsed = payload.map(parseDefinition)
  if (parsed.some((item) => item === null)) throw new Error('脚本入参响应格式暂不支持。')
  return parsed.filter((item): item is ScriptParameterDefinition => item !== null)
}