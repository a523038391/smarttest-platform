export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }
export type JsonObject = { [key: string]: JsonValue }

export type WorkflowNodeType = 'http' | 'condition' | 'parallel'
export type WorkflowEdgeOutcome = 'always' | 'success' | 'failure' | 'true' | 'false'

export interface WorkflowNode {
  id: string
  name: string
  type: WorkflowNodeType
  x: number
  y: number
  config: JsonObject
}

export interface WorkflowEdge {
  id: string
  source: string
  target: string
  outcome: WorkflowEdgeOutcome
}

export interface DataFactoryWorkflow {
  id: string
  project_id: string
  name: string
  description: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  variables: JsonObject
  state_version: number
  created_at: string
  updated_at: string
}

export interface WorkflowCreateInput {
  project_id: string
  name: string
  description: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  variables: JsonObject
}

export interface WorkflowUpdateInput {
  project_id: string
  name: string
  description: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  variables: JsonObject
  state_version: number
}

export interface WorkflowRun {
  id: string
  workflow_id: string
  status: 'SUCCEEDED' | 'FAILED'
  node_results: JsonObject
  variables: JsonObject
  started_at: string
  finished_at: string | null
}

export interface WorkflowExecutionInput {
  variables: JsonObject
}

export interface WorkflowDebugInput extends WorkflowExecutionInput {
  node_id: string
}