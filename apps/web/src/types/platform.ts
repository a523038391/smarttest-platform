export type NavKey =
  | 'dashboard'
  | 'requirements'
  | 'cases'
  | 'automation'
  | 'plans'
  | 'executions'
  | 'defects'
  | 'reports'
  | 'version-control'

export type IconName = NavKey | 'menu' | 'bell' | 'refresh' | 'search' | 'close'

export interface NavigationItem {
  key: NavKey
  label: string
  icon: IconName
}

export type Engine = 'http' | 'pytest' | 'playwright'

export type RunState =
  | 'CREATED'
  | 'QUEUED'
  | 'DISPATCHING'
  | 'RUNNING'
  | 'CANCELLING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'TIMED_OUT'
  | 'INFRA_ERROR'

export interface RunResponse {
  id: string
  engine: Engine
  parameters: Record<string, unknown>
  state: RunState
  created_at: string
  updated_at: string
  state_version: number
}

export type RunEventType =
  | 'started'
  | 'progress'
  | 'log'
  | 'assertion'
  | 'screenshot'
  | 'error'
  | 'heartbeat'
  | 'artifact'
  | 'finished'

export interface RunEvent {
  cursor: number
  event_id: string
  run_id: string
  attempt_id: string
  engine: Engine
  seq: number
  occurred_at: string
  type: RunEventType
  payload: Record<string, unknown>
}

export interface RunEventsResponse {
  items: RunEvent[]
  total: number
  next_cursor: number
}

export const navigationItems: NavigationItem[] = [
  { key: 'dashboard', label: '工作台', icon: 'dashboard' },
  { key: 'requirements', label: '需求管理', icon: 'requirements' },
  { key: 'cases', label: '测试用例', icon: 'cases' },
  { key: 'automation', label: '自动化', icon: 'automation' },
  { key: 'plans', label: '测试计划', icon: 'plans' },
  { key: 'executions', label: '执行中心', icon: 'executions' },
  { key: 'defects', label: '缺陷管理', icon: 'defects' },
  { key: 'reports', label: '测试报告', icon: 'reports' },
  { key: 'version-control', label: '代码版本', icon: 'version-control' },
]