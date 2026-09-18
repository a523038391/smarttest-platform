export type EnvironmentStatus = 'DRAFT' | 'ACTIVE' | 'ARCHIVED'
export type ConfigurationJson = null | boolean | number | string | ConfigurationJson[] | {
  [key: string]: ConfigurationJson
}

export interface PublicConfigurationValue {
  name: string
  secret: false
  value: ConfigurationJson
}

export interface SecretConfigurationValue {
  name: string
  secret: true
  configured: true
  secret_ref: string
}

export type ConfigurationValue = PublicConfigurationValue | SecretConfigurationValue

export type ConfigurationValueWrite =
  | { name: string; value: ConfigurationJson; secret?: false }
  | { name: string; value: string; secret: true; secret_ref?: never }
  | { name: string; secret: true; secret_ref: string; value?: never }

export interface Environment {
  id: string
  project_id: string
  name: string
  status: EnvironmentStatus
  revision: number
  state_version: number
  environment_variables: ConfigurationValue[]
  common_parameters: ConfigurationValue[]
  created_at: string
  updated_at: string
}

export interface EnvironmentCreateInput {
  project_id: string
  name: string
  environment_variables: ConfigurationValueWrite[]
  common_parameters: ConfigurationValueWrite[]
}

export interface EnvironmentUpdateInput extends Omit<EnvironmentCreateInput, 'project_id'> {
  status: EnvironmentStatus
  state_version: number
}