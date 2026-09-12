export interface VersionControlStatus {
  enabled: boolean
  repository_present: boolean
  branch: string | null
  head_short: string | null
  clean: boolean | null
  change_count: number
  changed_paths: string[]
  remote_configured: boolean
}

export interface VersionControlPublishInput {
  commit_message: string
}