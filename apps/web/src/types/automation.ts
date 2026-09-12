import type { Engine } from './platform'

export type AutomationScriptStatus = 'DRAFT' | 'ACTIVE' | 'ARCHIVED'

export interface AutomationScript {
  id: string
  project_id: string
  name: string
  description: string
  engine: Engine
  entrypoint: string
  source_ref: string
  content_digest: string
  timeout_seconds: number
  status: AutomationScriptStatus
  revision: number
  state_version: number
  created_at: string
  updated_at: string
}

export interface AutomationScriptCreateInput {
  project_id: string
  name: string
  description: string
  engine: Engine
  entrypoint: string
  source_ref: string
  content_digest: string
  timeout_seconds: number
}

export interface AutomationScriptUpdateInput {
  name: string
  description: string
  entrypoint: string
  source_ref: string
  content_digest: string
  timeout_seconds: number
  status: AutomationScriptStatus
  state_version: number
}
