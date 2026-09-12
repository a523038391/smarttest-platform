<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppHeader from './components/AppHeader.vue'
import AppSidebar from './components/AppSidebar.vue'
import AuthView from './components/AuthView.vue'
import AutomationView from './components/AutomationView.vue'
import DashboardView from './components/DashboardView.vue'
import DataFactoryView from './components/DataFactoryView.vue'
import ExecutionCenter from './components/ExecutionCenter.vue'
import PlaceholderView from './components/PlaceholderView.vue'
import TestPlanView from './components/TestPlanView.vue'
import VersionControlView from './components/VersionControlView.vue'
import { fetchAuthStatus, getAuthErrorMessage, logout } from './services/auth'
import {
  createProject as createProjectApi,
  deleteProject as deleteProjectApi,
  fetchProjects,
  getProjectErrorMessage,
} from './services/projects'
import type { AuthUser } from './types/auth'
import { navigationItems, type NavKey } from './types/platform'
import type { Project, ProjectCreateInput } from './types/project'

const PROJECT_STORAGE_KEY = 'smarttest:selected-project-id'

const activePage = ref<NavKey>('dashboard')
const latestRunIds = ref<string[]>([])
const sidebarOpen = ref(false)
const authLoading = ref(true)
const authError = ref('')
const setupRequired = ref(false)
const user = ref<AuthUser | null>(null)
const logoutPending = ref(false)
let statusController: AbortController | null = null
const pageTitle = computed(() =>
  navigationItems.find((item) => item.key === activePage.value)?.label ?? '智能测试平台',
)

const projects = ref<Project[]>([])
const projectsLoading = ref(false)
const projectsError = ref('')
const selectedProjectId = ref(localStorage.getItem(PROJECT_STORAGE_KEY) ?? '')
const creatingProject = ref(false)
const createProjectError = ref('')
const deletingProject = ref(false)
const deleteProjectError = ref('')
let projectsController: AbortController | null = null

async function loadAuthStatus() {
  statusController?.abort()
  statusController = new AbortController()
  authLoading.value = true
  authError.value = ''
  try {
    const status = await fetchAuthStatus(statusController.signal)
    setupRequired.value = status.setup_required
    user.value = status.user
    if (status.user) void loadProjects()
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    authError.value = getAuthErrorMessage(reason)
  } finally {
    authLoading.value = false
  }
}

function handleAuthenticated(authenticatedUser: AuthUser) {
  user.value = authenticatedUser
  setupRequired.value = false
  authError.value = ''
  void loadProjects()
}

function handleUnauthorized() {
  user.value = null
  setupRequired.value = false
  sidebarOpen.value = false
}

async function handleLogout() {
  if (logoutPending.value) return
  logoutPending.value = true
  try {
    await logout()
  } finally {
    logoutPending.value = false
    handleUnauthorized()
  }
}

function navigate(key: NavKey) {
  activePage.value = key
  sidebarOpen.value = false
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function handlePlanExecuted(runIds: string[]) {
  latestRunIds.value = runIds
  navigate('executions')
}

async function loadProjects() {
  projectsController?.abort()
  projectsController = new AbortController()
  projectsLoading.value = true
  projectsError.value = ''
  try {
    const items = await fetchProjects('ACTIVE', projectsController.signal)
    projects.value = items
    if (!items.some((item) => item.id === selectedProjectId.value)) {
      selectedProjectId.value = items[0]?.id ?? ''
    }
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    projectsError.value = getProjectErrorMessage(reason)
  } finally {
    projectsLoading.value = false
  }
}

async function handleCreateProject(input: ProjectCreateInput) {
  creatingProject.value = true
  createProjectError.value = ''
  try {
    const project = await createProjectApi(input)
    projects.value = [...projects.value, project]
    selectedProjectId.value = project.id
  } catch (reason) {
    createProjectError.value = getProjectErrorMessage(reason)
  } finally {
    creatingProject.value = false
  }
}

async function handleDeleteProject(projectId: string) {
  if (deletingProject.value) return
  deletingProject.value = true
  deleteProjectError.value = ''
  try {
    await deleteProjectApi(projectId)
    const removedIndex = projects.value.findIndex((item) => item.id === projectId)
    const remaining = projects.value.filter((item) => item.id !== projectId)
    projects.value = remaining
    if (selectedProjectId.value === projectId) {
      selectedProjectId.value = remaining[Math.min(removedIndex, remaining.length - 1)]?.id ?? ''
    }
  } catch (reason) {
    deleteProjectError.value = getProjectErrorMessage(reason)
  } finally {
    deletingProject.value = false
  }
}

watch(selectedProjectId, (value) => {
  if (value) localStorage.setItem(PROJECT_STORAGE_KEY, value)
  else localStorage.removeItem(PROJECT_STORAGE_KEY)
})

onMounted(() => {
  window.addEventListener('smarttest:unauthorized', handleUnauthorized)
  void loadAuthStatus()
})

onBeforeUnmount(() => {
  statusController?.abort()
  projectsController?.abort()
  window.removeEventListener('smarttest:unauthorized', handleUnauthorized)
})
</script>

<template>
  <div v-if="authLoading" class="auth-loading" role="status" aria-live="polite">
    <span class="auth-loading-mark" aria-hidden="true"><span></span></span>
    <strong>智测云</strong><p>正在验证安全会话…</p>
  </div>
  <div v-else-if="authError" class="auth-start-error" role="alert">
    <span aria-hidden="true">!</span><h1>暂时无法进入平台</h1><p>{{ authError }}</p>
    <button type="button" @click="loadAuthStatus">重新连接</button>
  </div>
  <AuthView v-else-if="!user" :setup-required="setupRequired" @authenticated="handleAuthenticated" />
  <template v-else>
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <AppSidebar :active="activePage" :open="sidebarOpen" @navigate="navigate" @close="sidebarOpen = false" />
    <button v-if="sidebarOpen" class="sidebar-overlay" type="button" aria-label="关闭导航" @click="sidebarOpen = false"></button>
    <div class="app-shell">
      <AppHeader
        :page-title="pageTitle"
        :user="user"
        :logout-pending="logoutPending"
        :projects="projects"
        :selected-project-id="selectedProjectId"
        :projects-loading="projectsLoading"
        :creating-project="creatingProject"
        :create-project-error="createProjectError"
        :deleting-project="deletingProject"
        :delete-project-error="deleteProjectError"
        @menu="sidebarOpen = true"
        @logout="handleLogout"
        @select="selectedProjectId = $event"
        @create="handleCreateProject"
        @delete="handleDeleteProject"
      />
      <main id="main-content" tabindex="-1">
        <DashboardView v-if="activePage === 'dashboard'" @navigate="navigate" />
        <ExecutionCenter
          v-else-if="activePage === 'executions'"
          :focus-run-ids="latestRunIds"
        />
        <AutomationView
          v-else-if="activePage === 'automation'"
          :project-id="selectedProjectId"
          :projects-loading="projectsLoading"
          :projects-error="projectsError"
          @executed="handlePlanExecuted"
          @navigate-plans="navigate('plans')"
        />
        <TestPlanView
          v-else-if="activePage === 'plans'"
          :project-id="selectedProjectId"
          :projects-loading="projectsLoading"
          :projects-error="projectsError"
          @executed="handlePlanExecuted"
        />
        <DataFactoryView
          v-else-if="activePage === 'data-factory'"
          :project-id="selectedProjectId"
          :projects-loading="projectsLoading"
          :projects-error="projectsError"
        />
        <VersionControlView v-else-if="activePage === 'version-control'" />
        <PlaceholderView v-else :title="pageTitle" />
      </main>
    </div>
  </template>
</template>
