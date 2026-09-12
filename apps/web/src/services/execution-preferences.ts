import type { HostPlanExecutionOptions } from '../types/test-plan'

const HOST_EXECUTION_STORAGE_KEY = 'smarttest:host-execution-paths'
const DEFAULT_HOST_EXECUTION_PATHS: HostExecutionPaths = {
  projectDirectory: 'C:\\Users\\Administrator\\PycharmProjects\\wecharmer',
  pythonExecutable: 'C:\\Users\\Administrator\\PycharmProjects\\wecharmer\\.venv\\Scripts\\python.exe',
}

export interface HostExecutionPaths {
  projectDirectory: string
  pythonExecutable: string
}

export function loadHostExecutionPaths(): HostExecutionPaths {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(HOST_EXECUTION_STORAGE_KEY) ?? 'null')
    if (typeof stored !== 'object' || stored === null) throw new Error('invalid preferences')
    const value = stored as Record<string, unknown>
    return {
      projectDirectory: typeof value.projectDirectory === 'string' && value.projectDirectory
        ? value.projectDirectory : DEFAULT_HOST_EXECUTION_PATHS.projectDirectory,
      pythonExecutable: typeof value.pythonExecutable === 'string' && value.pythonExecutable
        ? value.pythonExecutable : DEFAULT_HOST_EXECUTION_PATHS.pythonExecutable,
    }
  } catch {
    return { ...DEFAULT_HOST_EXECUTION_PATHS }
  }
}

export function rememberHostExecutionPaths(options: HostPlanExecutionOptions): void {
  try {
    localStorage.setItem(HOST_EXECUTION_STORAGE_KEY, JSON.stringify({
      projectDirectory: options.project_directory,
      pythonExecutable: options.python_executable,
    }))
  } catch {
    // Execution succeeded; unavailable preference storage must not turn it into a UI failure.
  }
}