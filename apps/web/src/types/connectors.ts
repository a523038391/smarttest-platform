export type ConnectorKind = 'MCP' | 'HTTP'
export type ConnectorAuthType = 'NONE' | 'BEARER' | 'API_KEY'

export type ConnectorJson = null | boolean | number | string | ConnectorJson[] | {
  [key: string]: ConnectorJson
}

export interface ConnectorTool {
  name: string
  description: string
  input_schema: Record<string, ConnectorJson>
}

export interface Connector {
  id: string
  name: string
  description: string
  kind: ConnectorKind
  base_url: string
  auth_type: ConnectorAuthType
  auth_header: string
  credential_configured: boolean
  enabled: boolean
  timeout_seconds: number
  state_version: number
  tools: ConnectorTool[]
  last_tested_at: string | null
  last_test_success: boolean | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface ConnectorCreateInput {
  name: string
  description: string
  kind: ConnectorKind
  base_url: string
  auth_type: ConnectorAuthType
  auth_header: string
  credential?: string
  timeout_seconds: number
}

export interface ConnectorUpdateInput extends ConnectorCreateInput {
  state_version: number
}

export interface ConnectorTestResult {
  connector: Connector
  success: boolean
  message: string
  latency_ms: number
  tool_count: number
}
