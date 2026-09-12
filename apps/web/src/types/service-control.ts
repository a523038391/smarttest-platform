export type ServiceRestartTarget = 'frontend' | 'backend' | 'all'

export interface ServiceControlStatus {
  enabled: boolean
  supervisor_online: boolean
  auto_restart_enabled: boolean
}

export interface ServiceRestartResponse {
  accepted: true
  target: ServiceRestartTarget
  reconnect_after_seconds: number
}