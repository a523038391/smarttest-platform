export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }
export type JsonObject = { [key: string]: JsonValue }

export type LoadTestMode = 'COUNT' | 'DURATION'
export type LoadTestTrafficMode = 'REQUESTS' | 'SCENARIO'
export type LoadTestRunStatus = 'RUNNING' | 'SUCCEEDED' | 'FAILED'
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS'

export interface LoadTestTarget {
  name: string
  method: HttpMethod
  url: string
  headers: JsonObject
  query: JsonObject
  body: JsonValue
  expected_statuses: number[]
  extractors: Record<string, string>
  think_time_ms: number
}

export interface LoadTestDefinition {
  id: string
  project_id: string
  environment_id: string | null
  name: string
  description: string
  targets: LoadTestTarget[]
  traffic_mode: LoadTestTrafficMode
  initial_variables: JsonObject
  stop_on_failure: boolean
  mode: LoadTestMode
  request_count: number
  duration_seconds: number
  concurrency: number
  interval_ms: number
  timeout_seconds: number
  state_version: number
  created_at: string
  updated_at: string
}

export interface LoadTestCreateInput {
  project_id: string
  environment_id: string | null
  name: string
  description: string
  targets: LoadTestTarget[]
  traffic_mode: LoadTestTrafficMode
  initial_variables: JsonObject
  stop_on_failure: boolean
  mode: LoadTestMode
  request_count: number
  duration_seconds: number
  concurrency: number
  interval_ms: number
  timeout_seconds: number
}

export interface LoadTestUpdateInput extends Omit<LoadTestCreateInput, 'project_id'> {
  state_version: number
}

export interface LoadTestMetricSummary {
  total_requests: number
  successful_requests: number
  failed_requests: number
  requests_per_second: number
  average_ms: number
  min_ms: number
  max_ms: number
  p50_ms: number
  p95_ms: number
  p99_ms: number
  status_counts: Record<string, number>
  error_counts: Record<string, number>
}

export interface LoadTestMetrics extends LoadTestMetricSummary {
  target_metrics: Record<string, LoadTestMetricSummary>
  scenario_metrics: LoadTestScenarioMetrics | null
}

export interface LoadTestScenarioMetrics {
  total_scenarios: number
  successful_scenarios: number
  failed_scenarios: number
  scenarios_per_second: number
  average_ms: number
  min_ms: number
  max_ms: number
  p50_ms: number
  p95_ms: number
  p99_ms: number
}

export interface LoadTestRun {
  id: string
  load_test_id: string
  status: LoadTestRunStatus
  mode: LoadTestMode
  started_at: string
  finished_at: string | null
  metrics: LoadTestMetrics | null
  error_message: string | null
}