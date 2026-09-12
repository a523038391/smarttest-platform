export type JsonScalar = string | number | boolean | null
export type ParameterInputType = 'text' | 'number' | 'integer' | 'boolean' | 'enum'

export interface ParameterEnumOption {
  label: string
  value: JsonScalar
}

export interface ParameterEnumSet {
  id: string
  project_id: string
  name: string
  description: string
  options: ParameterEnumOption[]
  state_version: number
  created_at: string
  updated_at: string
}

export interface ParameterEnumCreateInput {
  project_id: string
  name: string
  description: string
  options: ParameterEnumOption[]
}

export interface ParameterEnumUpdateInput {
  name: string
  description: string
  options: ParameterEnumOption[]
  state_version: number
}

export interface ScriptParameterDefinition {
  name: string
  label: string
  input_type: ParameterInputType
  required: boolean
  enum_set_id: string | null
  default_value: JsonScalar
  position: number
}