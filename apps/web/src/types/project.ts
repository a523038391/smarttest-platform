export type ProjectStatus = 'ACTIVE' | 'ARCHIVED'

export interface Project {
  id: string
  name: string
  description: string
  status: ProjectStatus
  state_version: number
  created_at: string
  updated_at: string
}

export interface ProjectCreateInput {
  name: string
  description: string
}
