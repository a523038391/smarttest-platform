<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AuthUser } from '../types/auth'
import type { Project, ProjectCreateInput } from '../types/project'
import AppIcon from './AppIcon.vue'

const props = defineProps<{
  pageTitle: string
  user: AuthUser
  logoutPending: boolean
  projects: Project[]
  selectedProjectId: string
  projectsLoading: boolean
  creatingProject: boolean
  createProjectError: string
  deletingProject: boolean
  deleteProjectError: string
}>()
const emit = defineEmits<{
  menu: []
  logout: []
  select: [projectId: string]
  create: [input: ProjectCreateInput]
  delete: [projectId: string]
}>()
const avatarText = computed(() => props.user.display_name.trim().charAt(0) || props.user.username.charAt(0))

const showCreateDialog = ref(false)
const newProjectName = ref('')
const newProjectDescription = ref('')
const deleteTarget = ref<Project | null>(null)
const CREATE_OPTION = '__create__'

function handleSelectChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value === CREATE_OPTION) {
    openCreateDialog()
    return
  }
  emit('select', value)
}

function openCreateDialog() {
  deleteTarget.value = null
  newProjectName.value = ''
  newProjectDescription.value = ''
  showCreateDialog.value = true
}

function submitCreateDialog() {
  const name = newProjectName.value.trim()
  if (!name) return
  emit('create', { name, description: newProjectDescription.value.trim() })
}

function openDeleteDialog() {
  const project = props.projects.find((item) => item.id === props.selectedProjectId)
  if (!project) return
  showCreateDialog.value = false
  deleteTarget.value = project
}

function submitDeleteDialog() {
  if (deleteTarget.value) emit('delete', deleteTarget.value.id)
}

watch(() => props.creatingProject, (isCreating, wasCreating) => {
  if (wasCreating && !isCreating && !props.createProjectError) showCreateDialog.value = false
})

watch(() => props.deletingProject, (isDeleting, wasDeleting) => {
  if (wasDeleting && !isDeleting && !props.deleteProjectError) deleteTarget.value = null
})
</script>

<template>
  <header class="topbar">
    <div class="title-area">
      <button class="menu-button" type="button" aria-label="打开主导航" @click="$emit('menu')">
        <AppIcon name="menu" />
      </button>
      <div><span>智能测试平台</span><h1>{{ pageTitle }}</h1></div>
    </div>
    <div class="top-actions">
      <div class="project-select-wrap">
        <label class="project-select">
          <span class="sr-only">选择项目</span>
          <span class="project-dot" aria-hidden="true"></span>
          <select
            :value="selectedProjectId"
            aria-label="当前项目"
            :disabled="projectsLoading"
            @change="handleSelectChange"
          >
            <option value="" disabled>{{ projectsLoading ? '加载项目中…' : '选择项目' }}</option>
            <option v-for="project in projects" :key="project.id" :value="project.id">
              {{ project.name }}
            </option>
            <option :value="CREATE_OPTION">+ 新建项目</option>
          </select>
        </label>
        <button
          class="project-delete-trigger"
          type="button"
          :disabled="projectsLoading || !selectedProjectId"
          title="删除当前项目"
          @click="openDeleteDialog"
        >删除</button>
        <div v-if="showCreateDialog" class="project-create-dialog" role="dialog" aria-label="新建项目">
          <h3>新建项目</h3>
          <label class="dialog-field">
            <span>项目名称</span>
            <input v-model="newProjectName" type="text" maxlength="255" placeholder="例如：结算平台" :disabled="creatingProject">
          </label>
          <label class="dialog-field">
            <span>项目描述（可选）</span>
            <input v-model="newProjectDescription" type="text" maxlength="500" placeholder="简要描述" :disabled="creatingProject">
          </label>
          <p v-if="createProjectError" class="dialog-error" role="alert">{{ createProjectError }}</p>
          <div class="dialog-actions">
            <button type="button" class="dialog-cancel" :disabled="creatingProject" @click="showCreateDialog = false">取消</button>
            <button type="button" class="dialog-submit" :disabled="creatingProject || !newProjectName.trim()" @click="submitCreateDialog">
              {{ creatingProject ? '创建中…' : '创建' }}
            </button>
          </div>
        </div>
        <div v-if="deleteTarget" class="project-create-dialog project-delete-dialog" role="dialog" aria-label="删除项目">
          <h3>删除项目</h3>
          <p>确认删除“{{ deleteTarget.name }}”吗？只有不包含业务数据的空项目才能删除。</p>
          <p v-if="deleteProjectError" class="dialog-error" role="alert">{{ deleteProjectError }}</p>
          <div class="dialog-actions">
            <button type="button" class="dialog-cancel" :disabled="deletingProject" @click="deleteTarget = null">取消</button>
            <button type="button" class="dialog-delete" :disabled="deletingProject" @click="submitDeleteDialog">
              {{ deletingProject ? '删除中…' : '确认删除' }}
            </button>
          </div>
        </div>
      </div>
      <button class="icon-button" type="button" aria-label="通知">
        <AppIcon name="bell" /><span class="notification-dot" aria-hidden="true"></span>
      </button>
      <div class="divider"></div>
      <div class="user-summary" :aria-label="`当前用户：${user.display_name}`">
        <span class="avatar" aria-hidden="true">{{ avatarText }}</span>
        <span class="user-copy"><strong>{{ user.display_name }}</strong><small>{{ user.username }} · 管理员</small></span>
      </div>
      <button class="logout-button" type="button" :disabled="logoutPending" @click="$emit('logout')">
        {{ logoutPending ? '退出中…' : '退出登录' }}
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar{height:76px;padding:0 30px;display:flex;align-items:center;justify-content:space-between;gap:24px;background:#fff;border-bottom:1px solid #e8edf5;box-sizing:border-box;position:sticky;top:0;z-index:20}
.title-area,.top-actions,.user-summary,.project-select{display:flex;align-items:center}.title-area{gap:12px}.title-area>div>span{display:none}.title-area h1{margin:0;color:#182236;font-size:18px;line-height:1.2;font-weight:650}
.menu-button{display:none}.top-actions{gap:12px}.project-select{height:40px;padding:0 6px 0 13px;border:1px solid #e1e7f0;border-radius:9px;background:#f8fafc}.project-dot{width:8px;height:8px;border-radius:50%;background:#5d89f7;box-shadow:0 0 0 4px #e8efff}.project-select select{min-width:132px;padding:0 25px 0 11px;border:0;outline:0;color:#354158;background:transparent;font:500 13px inherit;cursor:pointer}
.icon-button,.menu-button{position:relative;width:40px;height:40px;border:1px solid #e5eaf2;border-radius:9px;color:#69758a;background:#fff;cursor:pointer}.icon-button svg,.menu-button svg{width:19px;height:19px}.notification-dot{position:absolute;right:8px;top:8px;width:6px;height:6px;border:2px solid #fff;border-radius:50%;background:#ff5c6c}.divider{width:1px;height:28px;background:#e8edf3;margin:0 2px}
.user-summary{gap:9px;color:#556176}.avatar{width:36px;height:36px;border-radius:10px;background:linear-gradient(145deg,#dfe8ff,#edf2ff);color:#4f72ca;display:grid;place-items:center;font-size:13px;font-weight:700}.user-copy{display:flex;flex-direction:column;min-width:68px}.user-copy strong{max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:#263248}.user-copy small{font-size:10px;color:#98a2b3;margin-top:2px}.logout-button{height:34px;padding:0 11px;border:1px solid #e2e7ef;border-radius:8px;color:#667287;background:#fff;font-size:11px;font-weight:600;cursor:pointer}.logout-button:hover:not(:disabled){color:#c04654;border-color:#f0cbd0;background:#fff8f8}.logout-button:disabled{cursor:wait;opacity:.65}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:800px){.topbar{height:68px;padding:0 16px}.menu-button{display:grid;place-items:center}.project-select{display:none}.divider,.user-copy{display:none}.title-area h1{font-size:16px}.top-actions{gap:7px}.logout-button{padding:0 9px}}
@media(max-width:480px){.icon-button,.user-summary{display:none}.logout-button{font-size:10px}}
.project-select-wrap{position:relative;display:flex;align-items:center;gap:7px}.project-select select:disabled{cursor:wait;opacity:.7}
.project-delete-trigger{height:34px;padding:0 9px;border:1px solid #f0cbd0;border-radius:8px;color:#bf4653;background:#fff8f8;font-size:11px;font-weight:600;cursor:pointer}.project-delete-trigger:hover:not(:disabled){background:#fff0f1}.project-delete-trigger:disabled{cursor:not-allowed;opacity:.5}
.project-create-dialog{position:absolute;top:calc(100% + 8px);left:0;width:260px;padding:16px;border:1px solid #e5eaf2;border-radius:12px;background:#fff;box-shadow:0 16px 40px rgba(28,43,75,.16);z-index:30}
.project-create-dialog h3{margin:0 0 12px;color:#1c273a;font-size:14px}
.dialog-field{display:grid;gap:5px;margin-bottom:10px;font-size:11px;color:#556176}
.dialog-field input{height:36px;padding:0 10px;border:1px solid #dfe5ee;border-radius:7px;color:#253044;background:#fbfcfe;font-size:12px}
.dialog-field input:focus{border-color:#6b8de2;outline:0}
.dialog-error{margin:0 0 10px;padding:8px 10px;border:1px solid #ffd7db;border-radius:7px;color:#b84350;background:#fff5f6;font-size:11px;line-height:1.5}
.dialog-actions{display:flex;justify-content:flex-end;gap:8px}
.dialog-cancel,.dialog-submit{height:32px;padding:0 12px;border-radius:7px;font-size:11px;font-weight:600;cursor:pointer}
.dialog-cancel{border:1px solid #e2e7ef;color:#667287;background:#fff}
.dialog-submit{border:0;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc)}
.dialog-delete{height:32px;padding:0 12px;border:0;border-radius:7px;color:#fff;background:#d44f5d;font-size:11px;font-weight:600;cursor:pointer}.project-delete-dialog p{margin:0 0 12px;color:#657086;font-size:11px;line-height:1.6}
.dialog-submit:disabled,.dialog-cancel:disabled,.dialog-delete:disabled{cursor:wait;opacity:.65}
</style>