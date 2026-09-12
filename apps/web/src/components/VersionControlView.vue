<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  fetchServiceControlStatus,
  getServiceControlErrorMessage,
  restartService,
} from '../services/service-control'
import {
  fetchVersionControlStatus,
  getVersionControlErrorMessage,
  publishVersionControl,
  pullVersionControl,
} from '../services/version-control'
import type { ServiceControlStatus, ServiceRestartTarget } from '../types/service-control'
import type { VersionControlStatus } from '../types/version-control'

type ActionKind = 'pull' | 'publish' | null

const status = ref<VersionControlStatus | null>(null)
const loading = ref(true)
const loadError = ref('')
const serviceStatus = ref<ServiceControlStatus | null>(null)
const serviceLoading = ref(true)
const serviceLoadError = ref('')
const commitMessage = ref('')
const actionKind = ref<ActionKind>(null)
const actionError = ref('')
const actionSuccess = ref('')
const restartTarget = ref<ServiceRestartTarget | null>(null)
const restartError = ref('')
const restartSuccess = ref('')
let controller: AbortController | null = null
let serviceController: AbortController | null = null
let actionController: AbortController | null = null
let restartController: AbortController | null = null
let recoveryTimer: number | null = null

const repositoryReady = computed(() => Boolean(status.value?.enabled && status.value.repository_present))
const canPull = computed(() => Boolean(repositoryReady.value
  && status.value?.remote_configured && status.value.clean
  && !actionKind.value && !restartTarget.value))
const canPublish = computed(() => Boolean(repositoryReady.value
  && status.value?.remote_configured && commitMessage.value.trim()
  && !actionKind.value && !restartTarget.value))
const serviceReady = computed(() => Boolean(
  serviceStatus.value?.enabled && serviceStatus.value.supervisor_online,
))
const canRestart = computed(() => Boolean(serviceReady.value
  && !restartTarget.value && !actionKind.value))
const refreshing = computed(() => loading.value || serviceLoading.value)

async function loadStatus() {
  controller?.abort()
  const currentController = new AbortController()
  controller = currentController
  loading.value = true
  loadError.value = ''
  try {
    status.value = await fetchVersionControlStatus(currentController.signal)
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    status.value = null
    loadError.value = getVersionControlErrorMessage(reason)
  } finally {
    if (controller === currentController) loading.value = false
  }
}

async function loadServiceStatus() {
  serviceController?.abort()
  const currentController = new AbortController()
  serviceController = currentController
  serviceLoading.value = true
  serviceLoadError.value = ''
  try {
    serviceStatus.value = await fetchServiceControlStatus(currentController.signal)
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    serviceStatus.value = null
    serviceLoadError.value = getServiceControlErrorMessage(reason)
  } finally {
    if (serviceController === currentController) serviceLoading.value = false
  }
}

async function loadAllStatuses(showSuccess = false, preserveFeedback = false) {
  if (!preserveFeedback) {
    actionError.value = ''
    actionSuccess.value = ''
    restartError.value = ''
    restartSuccess.value = ''
  }
  await Promise.all([loadStatus(), loadServiceStatus()])
  if (showSuccess && !loadError.value && !serviceLoadError.value) {
    actionSuccess.value = '代码与服务控制状态已刷新。'
  }
}

function scheduleRecoveryRefresh() {
  if (recoveryTimer !== null) window.clearTimeout(recoveryTimer)
  recoveryTimer = window.setTimeout(() => {
    recoveryTimer = null
    void loadAllStatuses(false, true)
  }, 4000)
}

async function pullCode() {
  if (!canPull.value || !status.value) return
  if (!window.confirm(`确定从远程仓库拉取“${status.value.branch || '当前分支'}”的最新代码吗？`)) return
  actionController = new AbortController()
  actionKind.value = 'pull'
  actionError.value = ''
  actionSuccess.value = ''
  try {
    const nextStatus = await pullVersionControl(actionController.signal)
    status.value = nextStatus
    actionSuccess.value = nextStatus.restart_scheduled
      ? '代码同步成功，前后端即将自动重启。'
      : '代码拉取成功，工作区状态已更新。'
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    actionError.value = getVersionControlErrorMessage(reason)
  } finally {
    actionKind.value = null
    actionController = null
  }
}

async function publishCode() {
  const message = commitMessage.value.trim()
  if (!canPublish.value || !status.value || !message) return
  const confirmation = status.value.change_count > 0
    ? `确定提交当前 ${status.value.change_count} 项修改并上传到远程仓库吗？`
    : '工作区没有未提交修改。确定上传当前分支中尚未发布的提交吗？'
  if (!window.confirm(confirmation)) return
  actionController = new AbortController()
  actionKind.value = 'publish'
  actionError.value = ''
  actionSuccess.value = ''
  try {
    const nextStatus = await publishVersionControl(
      { commit_message: message }, actionController.signal,
    )
    status.value = nextStatus
    commitMessage.value = ''
    actionSuccess.value = nextStatus.restart_scheduled
      ? '代码同步成功，前后端即将自动重启。'
      : '代码上传成功，修改已提交并发布到远程仓库。'
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    actionError.value = getVersionControlErrorMessage(reason)
  } finally {
    actionKind.value = null
    actionController = null
  }
}

async function restart(target: ServiceRestartTarget) {
  if (!canRestart.value) return
  const targetName = target === 'frontend' ? '前端' : target === 'backend' ? '后端' : '前后端全部服务'
  if (!window.confirm(`确定重启${targetName}吗？重启期间服务可能短暂不可用。`)) return
  restartController = new AbortController()
  restartTarget.value = target
  restartError.value = ''
  restartSuccess.value = ''
  try {
    const result = await restartService(target, restartController.signal)
    if (target === 'frontend') {
      restartSuccess.value = '前端重启请求已接受，浏览器会自动恢复。'
    } else {
      const serviceName = target === 'backend' ? '后端' : '前后端'
      restartSuccess.value = `${serviceName}重启请求已接受，预计约 ${result.reconnect_after_seconds} 秒后重启，页面会自动重连。`
      scheduleRecoveryRefresh()
    }
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    restartError.value = getServiceControlErrorMessage(reason)
  } finally {
    restartTarget.value = null
    restartController = null
  }
}

onMounted(() => void loadAllStatuses())
onBeforeUnmount(() => {
  controller?.abort()
  serviceController?.abort()
  actionController?.abort()
  restartController?.abort()
  if (recoveryTimer !== null) window.clearTimeout(recoveryTimer)
})
</script>

<template>
  <section class="version-control" aria-labelledby="version-control-title">
    <header class="page-header">
      <div>
        <span class="eyebrow">VERSION CONTROL</span>
        <h2 id="version-control-title">代码版本</h2>
        <p>查看当前代码状态，并以受控方式拉取或上传代码。</p>
      </div>
      <button class="refresh-button" type="button" :disabled="refreshing || Boolean(actionKind) || Boolean(restartTarget)" @click="loadAllStatuses(true)">
        {{ refreshing ? '刷新中…' : '刷新状态' }}
      </button>
    </header>

    <section class="panel service-panel" aria-labelledby="service-control-title">
      <div class="panel-heading service-heading">
        <div><h3 id="service-control-title">服务控制</h3><p>通过预设操作安全地重启平台服务。</p></div>
        <span class="service-badge">SERVICE</span>
      </div>
      <div v-if="serviceLoading" class="service-state" role="status" aria-live="polite">
        <span class="mini-loader" aria-hidden="true"></span>正在读取服务控制状态…
      </div>
      <div v-else-if="serviceLoadError" class="service-state service-error" role="alert">
        <span>!</span><div><strong>服务控制状态不可用</strong><p>{{ serviceLoadError }}</p></div>
        <button type="button" @click="loadServiceStatus">重试</button>
      </div>
      <template v-else-if="serviceStatus">
        <div v-if="!serviceStatus.enabled" class="guidance warning service-guidance" role="status">
          <span aria-hidden="true">i</span><div><strong>服务控制功能尚未启用</strong><p>请联系管理员在服务端启用服务控制；启用前不能从此页面发起重启。</p></div>
        </div>
        <div v-else-if="!serviceStatus.supervisor_online" class="guidance warning service-guidance" role="status">
          <span aria-hidden="true">i</span><div><strong>服务守护进程当前离线</strong><p>请联系管理员检查守护进程状态，恢复在线后即可使用固定重启操作。</p></div>
        </div>
        <div class="service-overview" aria-label="服务控制概览">
          <div><span>守护进程</span><strong :class="serviceStatus.supervisor_online ? 'positive' : 'offline'">{{ serviceStatus.supervisor_online ? '在线' : '离线' }}</strong></div>
          <div><span>提交后自动重启</span><strong :class="serviceStatus.auto_restart_enabled ? 'positive' : 'muted'">{{ serviceStatus.auto_restart_enabled ? '已启用' : '未启用' }}</strong></div>
        </div>
        <div class="restart-actions" aria-label="服务重启操作">
          <button type="button" :disabled="!canRestart" @click="restart('frontend')">{{ restartTarget === 'frontend' ? '重启中…' : '重启前端' }}</button>
          <button type="button" :disabled="!canRestart" @click="restart('backend')">{{ restartTarget === 'backend' ? '重启中…' : '重启后端' }}</button>
          <button class="restart-all" type="button" :disabled="!canRestart" @click="restart('all')">{{ restartTarget === 'all' ? '重启中…' : '全部重启' }}</button>
        </div>
        <p v-if="restartTarget" class="feedback pending" role="status" aria-live="polite"><span class="mini-loader" aria-hidden="true"></span>正在提交重启请求，请稍候…</p>
        <p v-if="restartSuccess" class="feedback success" role="status" aria-live="polite">✓ {{ restartSuccess }}</p>
        <p v-if="restartError" class="feedback error" role="alert">! {{ restartError }}</p>
        <p class="service-note">此处仅提供固定重启操作，不接收命令、PID 或路径。</p>
      </template>
    </section>

    <div v-if="loading" class="state-view" role="status" aria-live="polite">
      <span class="loader" aria-hidden="true"></span><strong>正在读取代码版本状态</strong>
      <p>正在安全地检查仓库、分支和工作区信息。</p>
    </div>
    <div v-else-if="loadError" class="state-view error-view" role="alert">
      <span class="state-icon error-icon" aria-hidden="true">!</span><strong>代码版本状态不可用</strong>
      <p>{{ loadError }}</p><button type="button" @click="loadStatus()">重新连接</button>
    </div>
    <template v-else-if="status">
      <div v-if="!status.enabled" class="guidance warning" role="status">
        <span aria-hidden="true">i</span><div><strong>代码版本功能尚未启用</strong><p>请联系管理员在服务端启用代码版本功能，启用后即可在此查看和同步代码。</p></div>
      </div>
      <div v-else-if="!status.repository_present" class="guidance warning" role="status">
        <span aria-hidden="true">i</span><div><strong>未检测到代码仓库</strong><p>请联系管理员检查服务部署目录与仓库配置。此页面不支持填写或修改仓库路径。</p></div>
      </div>
      <div v-else-if="!status.remote_configured" class="guidance warning" role="status">
        <span aria-hidden="true">i</span><div><strong>尚未配置远程仓库</strong><p>请联系管理员完成服务端远程仓库配置。此页面不会收集远程地址或访问凭据。</p></div>
      </div>

      <div class="status-grid" aria-label="代码版本概览">
        <article><span>当前分支</span><strong>{{ status.branch || '不可用' }}</strong></article>
        <article><span>当前版本</span><strong class="sha">{{ status.head_short || '不可用' }}</strong></article>
        <article><span>远程仓库</span><strong :class="status.remote_configured ? 'positive' : 'muted'">{{ status.remote_configured ? '已配置' : '未配置' }}</strong></article>
        <article><span>工作区</span><strong :class="status.clean ? 'positive' : status.clean === false ? 'changed' : 'muted'">{{ status.clean === null ? '不可用' : status.clean ? '干净' : `${status.change_count} 项修改` }}</strong></article>
      </div>

      <p v-if="actionKind" class="feedback pending" role="status" aria-live="polite">
        <span class="mini-loader" aria-hidden="true"></span>{{ actionKind === 'pull' ? '正在拉取代码，请稍候…' : '正在提交并上传代码，请稍候…' }}
      </p>
      <p v-if="actionSuccess" class="feedback success" role="status" aria-live="polite">✓ {{ actionSuccess }}</p>
      <p v-if="actionError" class="feedback error" role="alert">! {{ actionError }}</p>

      <div class="content-grid">
        <section class="panel files-panel" aria-labelledby="changed-files-title">
          <div class="panel-heading">
            <div><h3 id="changed-files-title">工作区文件</h3><p>当前检测到的修改文件</p></div>
            <span class="count">{{ status.change_count }}</span>
          </div>
          <div v-if="status.changed_paths.length" class="file-list">
            <div v-for="path in status.changed_paths" :key="path" class="file-item"><span aria-hidden="true">M</span><code>{{ path }}</code></div>
          </div>
          <div v-else class="empty-files"><span aria-hidden="true">✓</span><strong>工作区没有修改</strong><p>当前代码与已提交版本一致。</p></div>
        </section>

        <div class="action-stack">
          <section class="panel action-panel" aria-labelledby="pull-title">
            <div><h3 id="pull-title">拉取代码</h3><p>仅当工作区干净且已配置远程仓库时可以拉取。</p></div>
            <button class="secondary-action" type="button" :disabled="!canPull" @click="pullCode">{{ actionKind === 'pull' ? '拉取中…' : '拉取代码' }}</button>
          </section>
          <section class="panel publish-panel" aria-labelledby="publish-title">
            <div><h3 id="publish-title">上传代码</h3><p>填写本次修改说明，确认后提交并上传当前修改。</p></div>
            <label for="commit-message">提交说明</label>
            <textarea id="commit-message" v-model="commitMessage" rows="4" maxlength="120" :disabled="Boolean(actionKind) || !repositoryReady || !status.remote_configured" placeholder="例如：修复登录流程的错误提示"></textarea>
            <button class="primary-action" type="button" :disabled="!canPublish" @click="publishCode">{{ actionKind === 'publish' ? '上传中…' : '上传代码' }}</button>
          </section>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.version-control{display:flex;flex-direction:column;gap:18px}.page-header{display:flex;align-items:center;justify-content:space-between;gap:18px}.eyebrow{color:#6780bd;font-size:10px;font-weight:800;letter-spacing:.16em}.page-header h2{margin:5px 0 0;color:#1c273a;font-size:22px}.page-header p{margin:6px 0 0;color:#8994a7;font-size:12px;line-height:1.6}.refresh-button{height:38px;padding:0 15px;border:1px solid #dce3ed;border-radius:8px;color:#536078;background:#fff;font-size:12px;font-weight:700;cursor:pointer}.refresh-button:disabled{cursor:wait;opacity:.6}
.state-view{min-height:260px;padding:28px;border:1px solid #e7ecf3;border-radius:14px;background:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:7px}.state-view strong{color:#28354a;font-size:15px}.state-view p{max-width:440px;margin:0;color:#8d98aa;font-size:12px;line-height:1.7}.state-view button{margin-top:9px;height:36px;padding:0 14px;border:0;border-radius:8px;color:#fff;background:#526fca;font-weight:700;cursor:pointer}.error-view{border-color:#ffd7db}.loader,.mini-loader{border:3px solid #dbe4f5;border-top-color:#4f72ca;border-radius:50%;animation:spin .8s linear infinite}.loader{width:22px;height:22px}.mini-loader{width:14px;height:14px;border-width:2px}.state-icon{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:800}.error-icon{background:#df6070}@keyframes spin{to{transform:rotate(360deg)}}
.guidance{display:flex;gap:12px;padding:15px 17px;border:1px solid #f0d89e;border-radius:11px;background:#fffaf0}.guidance>span{width:25px;height:25px;flex:none;border-radius:50%;display:grid;place-items:center;color:#fff;background:#d69b27;font-weight:800}.guidance strong{color:#735719;font-size:13px}.guidance p{margin:3px 0 0;color:#8a7951;font-size:11px;line-height:1.6}.status-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.status-grid article{min-height:94px;padding:17px;border:1px solid #e7ecf3;border-radius:12px;background:#fff;box-shadow:0 4px 14px rgba(26,39,66,.03)}.status-grid span{display:block;margin-bottom:10px;color:#909bad;font-size:11px}.status-grid strong{color:#27344a;font-size:15px;word-break:break-word}.status-grid .sha{font-family:Consolas,monospace;letter-spacing:.04em}.status-grid .positive{color:#21865d}.status-grid .changed{color:#d07430}.status-grid .muted{color:#8b95a5}
.feedback{display:flex;align-items:center;gap:8px;margin:0;padding:11px 13px;border-radius:9px;font-size:12px;line-height:1.5}.feedback.pending{border:1px solid #d9e4fa;color:#4965a6;background:#f4f7ff}.feedback.success{border:1px solid #ccebd8;color:#287a49;background:#f2fbf5}.feedback.error{border:1px solid #ffd7db;color:#b84350;background:#fff5f6}.content-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(300px,.8fr);gap:16px;align-items:start}.panel{padding:20px;border:1px solid #e7ecf3;border-radius:13px;background:#fff}.panel h3{margin:0;color:#28354a;font-size:14px}.panel p{margin:5px 0 0;color:#8994a7;font-size:11px;line-height:1.6}.panel-heading{display:flex;align-items:center;justify-content:space-between;padding-bottom:15px;border-bottom:1px solid #eef1f6}.count{min-width:28px;height:25px;padding:0 8px;border-radius:13px;display:grid;place-items:center;color:#526fca;background:#edf2ff;font-size:11px;font-weight:800}.file-list{max-height:390px;overflow:auto}.file-item{display:flex;align-items:center;gap:10px;padding:12px 2px;border-bottom:1px solid #f0f3f7}.file-item:last-child{border-bottom:0}.file-item span{width:22px;height:22px;flex:none;border-radius:6px;display:grid;place-items:center;color:#b76b2e;background:#fff0e5;font-size:9px;font-weight:800}.file-item code{min-width:0;color:#46536a;font:11px/1.5 Consolas,"Courier New",monospace;overflow-wrap:anywhere}.empty-files{min-height:190px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}.empty-files>span{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;color:#21865d;background:#e9f8f1;font-weight:800}.empty-files strong{margin-top:9px;color:#405068;font-size:12px}.action-stack{display:grid;gap:16px}.action-panel{display:grid;gap:15px}.action-panel button,.publish-panel button{width:100%;height:40px;border-radius:9px;font-size:12px;font-weight:750;cursor:pointer}.secondary-action{border:1px solid #cfdaef;color:#4763a7;background:#f7f9ff}.primary-action{border:0;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc)}.action-panel button:disabled,.publish-panel button:disabled{cursor:not-allowed;opacity:.52}.publish-panel{display:grid;gap:12px}.publish-panel label{margin-bottom:-5px;color:#4f5d73;font-size:11px;font-weight:700}.publish-panel textarea{width:100%;resize:vertical;min-height:92px;padding:11px 12px;border:1px solid #dfe5ee;border-radius:9px;color:#344158;background:#fff;font:12px/1.6 inherit}.publish-panel textarea:focus{outline:3px solid rgba(80,121,223,.22);border-color:#7994dd}.publish-panel textarea:disabled{color:#8994a7;background:#f5f7fa;cursor:not-allowed}
.service-panel{display:grid;gap:16px}.service-heading{padding-bottom:14px}.service-badge{padding:5px 8px;border-radius:6px;color:#5872b3;background:#edf2ff;font-size:9px;font-weight:800;letter-spacing:.1em}.service-state{min-height:84px;display:flex;align-items:center;justify-content:center;gap:9px;color:#718097;font-size:12px}.service-error{justify-content:flex-start;padding:13px;border:1px solid #ffd7db;border-radius:9px;background:#fff5f6}.service-error>span{width:25px;height:25px;flex:none;border-radius:50%;display:grid;place-items:center;color:#fff;background:#df6070;font-weight:800}.service-error strong{color:#9f3945;font-size:12px}.service-error p{margin:2px 0 0;color:#b75b65}.service-error button{margin-left:auto;padding:7px 11px;border:1px solid #efbcc2;border-radius:7px;color:#a83f4b;background:#fff;cursor:pointer}.service-guidance{margin:0}.service-overview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.service-overview>div{padding:13px 15px;border:1px solid #e8edf4;border-radius:10px;background:#fafbfd}.service-overview span{display:block;margin-bottom:5px;color:#8a96a8;font-size:10px}.service-overview strong{color:#354258;font-size:13px}.service-overview .positive{color:#21865d}.service-overview .offline{color:#c0525d}.service-overview .muted{color:#8b95a5}.restart-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.restart-actions button{height:39px;border:1px solid #cfdaef;border-radius:9px;color:#4763a7;background:#f7f9ff;font-size:12px;font-weight:750;cursor:pointer}.restart-actions .restart-all{border:0;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc)}.restart-actions button:disabled{cursor:not-allowed;opacity:.5}.service-panel .feedback,.service-panel .service-note{margin:0}.service-panel .service-note{color:#9aa4b3;font-size:10px}
@media(max-width:980px){.status-grid{grid-template-columns:repeat(2,1fr)}.content-grid{grid-template-columns:1fr}.action-stack{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:620px){.page-header{align-items:flex-start;flex-direction:column}.refresh-button{width:100%}.status-grid,.action-stack,.service-overview,.restart-actions{grid-template-columns:1fr}.panel{padding:17px}.status-grid article{min-height:82px}.guidance{align-items:flex-start}.content-grid{gap:12px}.service-error{align-items:flex-start;flex-wrap:wrap}.service-error button{width:100%;margin-left:34px}}
</style>