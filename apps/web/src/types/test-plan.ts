export type TestPlanStatus = 'DRAFT' | 'ACTIVE' | 'ARCHIVED'
export type JsonRecord = Record<string, unknown>
export type ExecutionTarget = 'docker' | 'host'

export interface DockerPlanExecutionOptions {
  execution_target: 'docker'
}

export interface HostPlanExecutionOptions {
  execution_target: 'host'
  project_directory: string
  python_executable: string
}

export type PlanExecutionOptions = DockerPlanExecutionOptions | HostPlanExecutionOptions

export interface TestPlanDataRow {
  row_key: string
  values: JsonRecord
}

export interface TestPlanItem {
  item_id: string
  script_id: string
  script_revision: number
  environment_id: string | null
  environment_revision: number | null
  default_parameters: JsonRecord
  data_rows: TestPlanDataRow[]
  execution_policy: JsonRecord
}

export interface TestPlanResponse {
  id: string
  tenant_id: string
  project_id: string
  name: string
  description: string
  items: TestPlanItem[]
  max_parallel: number
  status: TestPlanStatus
  revision: number
  state_version: number
  created_at: string
  updated_at: string
}

export interface TestPlanItemInput {
  item_id?: string
  script_id: string
  script_revision: number
  environment_id?: string | null
  environment_revision?: number | null
  default_parameters?: JsonRecord
  data_rows?: TestPlanDataRow[] | null
  execution_policy?: JsonRecord
}

export interface TestPlanCreateInput {
  tenant_id?: string
  project_id: string
  name: string
  description: string
  items: TestPlanItemInput[]
  max_parallel: number
}

export interface TestPlanUpdateInput {
  name: string
  description: string
  items: TestPlanItemInput[]
  max_parallel: number
  status: TestPlanStatus
  state_version: number
}

export interface ExecutionBatch {
  id: string
  tenant_id: string
  project_id: string
  plan_id: string
  plan_revision: number
  idempotency_key: string
  max_parallel: number
  run_ids: string[]
  created_at: string
}