import type {
  Engine,
  RunEvent,
  RunEventsResponse,
  RunEventType,
  RunResponse,
  RunState,
} from '../types/platform'

type UnknownRecord = Record<string, unknown>
const engines: readonly Engine[] = ['http', 'pytest', 'playwright']
const runStates: readonly RunState[] = [
  'CREATED', 'QUEUED', 'DISPATCHING', 'RUNNING', 'CANCELLING',
  'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'INFRA_ERROR',
]
const runEventTypes: readonly RunEventType[] = [
  'started', 'progress', 'log', 'assertion', 'screenshot', 'error',
  'heartbeat', 'artifact', 'finished',
]

export type RunEventConnectionState = 'open' | 'reconnecting'

export interface RunEventSubscriptionOptions {
  after?: number
  onEvent: (event: RunEvent) => void
  onStateChange?: (state: RunEventConnectionState) => void
  onInvalidEvent?: (error: Error) => void
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isEngine(value: unknown): value is Engine {
  return typeof value === 'string' && engines.includes(value as Engine)
}

function isRunState(value: unknown): value is RunState {
  return typeof value === 'string' && runStates.includes(value as RunState)
}

function isRunEventType(value: unknown): value is RunEventType {
  return typeof value === 'string' && runEventTypes.includes(value as RunEventType)
}

function isDateTime(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function parseRun(value: unknown): RunResponse | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !isEngine(value.engine)
    || !isRecord(value.parameters) || !isRunState(value.state)
    || !isDateTime(value.created_at) || !isDateTime(value.updated_at)
    || !Number.isInteger(value.state_version) || (value.state_version as number) < 0) return null
  return {
    id: value.id,
    engine: value.engine,
    parameters: value.parameters,
    state: value.state,
    created_at: value.created_at,
    updated_at: value.updated_at,
    state_version: value.state_version as number,
  }
}

function parseRunEvent(value: unknown): RunEvent | null {
  if (!isRecord(value) || !Number.isInteger(value.cursor) || (value.cursor as number) < 0
    || typeof value.event_id !== 'string' || value.event_id.length === 0
    || typeof value.run_id !== 'string' || value.run_id.length === 0
    || typeof value.attempt_id !== 'string' || value.attempt_id.length === 0
    || !isEngine(value.engine) || !Number.isInteger(value.seq) || (value.seq as number) < 0
    || !isDateTime(value.occurred_at) || !isRunEventType(value.type)
    || !isRecord(value.payload)) return null
  return {
    cursor: value.cursor as number,
    event_id: value.event_id,
    run_id: value.run_id,
    attempt_id: value.attempt_id,
    engine: value.engine,
    seq: value.seq as number,
    occurred_at: value.occurred_at,
    type: value.type,
    payload: value.payload,
  }
}

async function createHttpError(response: Response): Promise<Error> {
  const fallback = `服务返回 HTTP ${response.status}`
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase()
  if (mediaType !== 'application/problem+json') return new Error(fallback)

  try {
    const problem: unknown = await response.json()
    if (!isRecord(problem)) return new Error(fallback)
    const detail = typeof problem.detail === 'string'
      ? problem.detail.trim().replace(/\s+/g, ' ').slice(0, 500) : ''
    const code = typeof problem.code === 'string'
      ? problem.code.trim().replace(/\s+/g, ' ').slice(0, 100) : ''
    const message = detail || fallback
    return new Error(code ? `${message}（${code}）` : message)
  } catch {
    return new Error(fallback)
  }
}

async function request(path: string, signal?: AbortSignal): Promise<Response> {
  const response = await fetch(path, {
    signal,
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  })
  if (response.status === 401) window.dispatchEvent(new Event('smarttest:unauthorized'))
  if (!response.ok) throw await createHttpError(response)
  return response
}

export async function checkReady(signal?: AbortSignal): Promise<void> {
  await request('/health/ready', signal)
}

export async function fetchRuns(signal?: AbortSignal): Promise<RunResponse[]> {
  const response = await request('/api/v1/runs', signal)
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new Error('运行列表未返回有效 JSON')
  }
  if (!isRecord(payload) || !Array.isArray(payload.items)
    || !Number.isInteger(payload.total) || (payload.total as number) < 0) {
    throw new Error('运行列表响应格式暂不支持')
  }
  const runs = payload.items.map(parseRun)
  if (runs.some((run) => run === null)) throw new Error('运行记录字段格式暂不支持')
  return runs.filter((run): run is RunResponse => run !== null)
}

export async function fetchRunEvents(
  runId: string,
  signal?: AbortSignal,
): Promise<RunEventsResponse> {
  const response = await request(`/api/v1/runs/${encodeURIComponent(runId)}/events`, signal)
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new Error('运行事件未返回有效 JSON')
  }
  if (!isRecord(payload) || !Array.isArray(payload.items)
    || !Number.isInteger(payload.total) || (payload.total as number) < 0
    || !Number.isInteger(payload.next_cursor) || (payload.next_cursor as number) < 0) {
    throw new Error('运行事件响应格式暂不支持')
  }
  const events = payload.items.map(parseRunEvent)
  if (events.some((event) => event === null)) throw new Error('运行事件字段格式暂不支持')
  const items = events.filter((event): event is RunEvent => event !== null)
  if (items.some((event) => event.run_id !== runId)) throw new Error('运行事件与当前运行不匹配')
  return { items, total: payload.total as number, next_cursor: payload.next_cursor as number }
}

export function subscribeToRunEvents(
  runId: string,
  options: RunEventSubscriptionOptions,
): EventSource {
  const after = options.after ?? 0
  if (!Number.isInteger(after) || after < 0) throw new Error('事件游标必须是非负整数')
  const source = new EventSource(
    `/api/v1/runs/${encodeURIComponent(runId)}/events/stream?after=${after}`,
  )
  source.onopen = () => options.onStateChange?.('open')
  source.onerror = (event) => {
    if (!(event instanceof MessageEvent)) options.onStateChange?.('reconnecting')
  }

  for (const type of runEventTypes) {
    source.addEventListener(type, (message) => {
      if (!(message instanceof MessageEvent)) return
      try {
        const parsed: unknown = JSON.parse(message.data as string)
        const event = parseRunEvent(parsed)
        const lastEventId = message.lastEventId
        if (!event || event.run_id !== runId || event.type !== type
          || (lastEventId !== '' && String(event.cursor) !== lastEventId)) {
          throw new Error('实时运行事件格式暂不支持')
        }
        options.onEvent(event)
      } catch (reason) {
        options.onInvalidEvent?.(
          reason instanceof Error ? reason : new Error('无法解析实时运行事件'),
        )
      }
    })
  }
  return source
}

export function getErrorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.name === 'AbortError') return '请求已取消'
  if (reason instanceof TypeError) return '无法连接到同源 API 服务'
  if (reason instanceof Error) return reason.message
  return '请求运行服务时发生未知错误'
}