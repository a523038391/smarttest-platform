<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import {
  checkReady,
  fetchRunEvents,
  fetchRuns,
  getErrorMessage,
  subscribeToRunEvents,
} from '../services/runs'
import type { RunEvent, RunResponse, RunState } from '../types/platform'

type ServiceState = 'loading' | 'online' | 'degraded' | 'offline'
type ConnectionState = 'idle' | 'loading' | 'connecting' | 'live' | 'complete' | 'reconnecting' | 'error'

const props = defineProps<{ focusRunIds?: string[] }>()

const runs = ref<RunResponse[]>([])
const serviceState = ref<ServiceState>('loading')
const serviceDetail = ref('正在检查 /health/ready 与 /api/v1/runs')
const runsError = ref('')
const search = ref('')
const statusFilter = ref('all')
const selectedRun = ref<RunResponse | null>(null)
const runEvents = ref<RunEvent[]>([])
const eventTotal = ref(0)
const connectionState = ref<ConnectionState>('idle')
const detailError = ref('')
const detailPanel = ref<HTMLElement | null>(null)
let activeController: AbortController | null = null
let eventsController: AbortController | null = null
let eventSource: EventSource | null = null
let selectionToken = 0

const statusInfo: Record<RunState, { label: string; tone: string }> = {
  CREATED: { label: '已创建', tone: 'neutral' },
  QUEUED: { label: '排队中', tone: 'pending' },
  DISPATCHING: { label: '调度中', tone: 'pending' },
  RUNNING: { label: '运行中', tone: 'running' },
  CANCELLING: { label: '取消中', tone: 'pending' },
  SUCCEEDED: { label: '成功', tone: 'success' },
  FAILED: { label: '失败', tone: 'danger' },
  CANCELLED: { label: '已取消', tone: 'neutral' },
  TIMED_OUT: { label: '已超时', tone: 'danger' },
  INFRA_ERROR: { label: '基础设施错误', tone: 'danger' },
}
const terminalStates: readonly RunState[] = [
  'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'INFRA_ERROR',
]

const filteredRuns = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  return runs.value.filter((run) => {
    const state = run.state
    const matchesStatus =
      statusFilter.value === 'all' ||
      (statusFilter.value === 'running' && ['CREATED', 'QUEUED', 'DISPATCHING', 'RUNNING', 'CANCELLING'].includes(state)) ||
      (statusFilter.value === 'success' && state === 'SUCCEEDED') ||
      (statusFilter.value === 'failed' && ['FAILED', 'TIMED_OUT', 'INFRA_ERROR'].includes(state))
    if (!matchesStatus) return false
    if (!keyword) return true
    return [run.id, run.engine, run.state]
      .some((value) => value.toLocaleLowerCase().includes(keyword))
  })
})

const progress = computed<number | null>(() => {
  for (let index = runEvents.value.length - 1; index >= 0; index -= 1) {
    const event = runEvents.value[index]
    if (event?.type !== 'progress') continue
    const percent = event.payload.percent
    if (typeof percent === 'number' && Number.isFinite(percent)) {
      return Math.min(100, Math.max(0, percent))
    }
  }
  return null
})
const logEvents = computed(() => runEvents.value.filter((event) => event.type === 'log'))
const errorEvents = computed(() => runEvents.value.filter((event) => event.type === 'error'))
const assetEvents = computed(() => runEvents.value.filter(
  (event) => event.type === 'screenshot' || event.type === 'artifact',
))

const connectionInfo: Record<ConnectionState, { label: string; detail: string }> = {
  idle: { label: '未连接', detail: '请选择运行' },
  loading: { label: '读取记录', detail: '正在加载历史事件' },
  connecting: { label: '正在连接', detail: '正在建立实时事件连接' },
  live: { label: '实时连接', detail: '新事件将自动显示' },
  complete: { label: '执行完成', detail: '全部持久化事件已加载' },
  reconnecting: { label: '正在重连', detail: '连接中断，浏览器正在自动重试' },
  error: { label: '连接异常', detail: '无法读取此运行的事件' },
}

async function refresh() {
  activeController?.abort()
  const controller = new AbortController()
  activeController = controller
  serviceState.value = 'loading'
  serviceDetail.value = '正在检查 /health/ready 与 /api/v1/runs'
  runsError.value = ''

  const [readyResult, runsResult] = await Promise.allSettled([
    checkReady(controller.signal),
    fetchRuns(controller.signal),
  ])
  if (activeController !== controller) return

  if (runsResult.status === 'fulfilled') {
    runs.value = runsResult.value
    if (selectedRun.value) {
      const updatedRun = runs.value.find((run) => run.id === selectedRun.value?.id)
      if (updatedRun) selectedRun.value = updatedRun
      else closeDetails()
    }
    if (!selectedRun.value && props.focusRunIds?.length) {
      const focused = props.focusRunIds
        .map((id) => runs.value.find((run) => run.id === id))
        .find((run): run is RunResponse => run !== undefined)
      if (focused) void selectRun(focused)
    }
  }
  else {
    runs.value = []
    runsError.value = getErrorMessage(runsResult.reason)
  }

  if (readyResult.status === 'fulfilled' && runsResult.status === 'fulfilled') {
    serviceState.value = 'online'
    serviceDetail.value = '同源 API 服务已就绪，运行数据已同步'
  } else if (readyResult.status === 'rejected' && runsResult.status === 'rejected') {
    serviceState.value = 'offline'
    serviceDetail.value = `API 离线：${getErrorMessage(readyResult.reason)}`
  } else {
    serviceState.value = 'degraded'
    serviceDetail.value = runsError.value
      ? `运行列表不可用：${runsError.value}`
      : `就绪检查异常：${getErrorMessage(readyResult.status === 'rejected' ? readyResult.reason : null)}`
  }
}

function stopEventConnection() {
  selectionToken += 1
  eventsController?.abort()
  eventsController = null
  eventSource?.close()
  eventSource = null
}

function mergeEvent(event: RunEvent) {
  const isNew = !runEvents.value.some(
    (current) => current.cursor === event.cursor || current.event_id === event.event_id,
  )
  const withoutDuplicate = runEvents.value.filter(
    (current) => current.cursor !== event.cursor && current.event_id !== event.event_id,
  )
  runEvents.value = [...withoutDuplicate, event].sort(
    (left, right) => left.cursor - right.cursor || left.seq - right.seq,
  )
  if (isNew) eventTotal.value += 1
}

async function syncFinishedRun(runId: string, token: number) {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      const updatedRuns = await fetchRuns()
      if (token !== selectionToken) return
      runs.value = updatedRuns
      const updatedRun = updatedRuns.find((run) => run.id === runId)
      if (updatedRun && selectedRun.value?.id === runId) selectedRun.value = updatedRun
      if (updatedRun && terminalStates.includes(updatedRun.state)) return
    } catch {
      // The event stream is already complete; retain its persisted events and retry.
    }
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

function connectToEvents(runId: string, after: number, token: number) {
  if (token !== selectionToken) return
  try {
    eventSource = subscribeToRunEvents(runId, {
      after,
      onEvent: (event) => {
        if (token !== selectionToken) return
        mergeEvent(event)
        if (event.type === 'finished') {
          eventSource?.close()
          eventSource = null
          connectionState.value = 'complete'
          void syncFinishedRun(runId, token)
        }
      },
      onStateChange: (state) => {
        if (token === selectionToken) connectionState.value = state === 'open' ? 'live' : 'reconnecting'
      },
      onInvalidEvent: (error) => {
        if (token === selectionToken) detailError.value = error.message
      },
    })
  } catch (reason) {
    if (token === selectionToken) {
      connectionState.value = 'error'
      detailError.value = getErrorMessage(reason)
    }
  }
}

async function selectRun(run: RunResponse) {
  if (selectedRun.value?.id === run.id) return
  stopEventConnection()
  const token = selectionToken
  selectedRun.value = run
  runEvents.value = []
  eventTotal.value = 0
  detailError.value = ''
  connectionState.value = 'loading'
  void nextTick(() => detailPanel.value?.focus())

  const controller = new AbortController()
  eventsController = controller
  try {
    const response = await fetchRunEvents(run.id, controller.signal)
    if (token !== selectionToken) return
    runEvents.value = [...response.items].sort(
      (left, right) => left.cursor - right.cursor || left.seq - right.seq,
    )
    eventTotal.value = response.total
    eventsController = null
    const hasFinished = response.items.some((event) => event.type === 'finished')
    if (terminalStates.includes(run.state) || hasFinished) {
      connectionState.value = 'complete'
      if (hasFinished && !terminalStates.includes(run.state)) {
        void syncFinishedRun(run.id, token)
      }
      return
    }
    connectionState.value = 'connecting'
    connectToEvents(run.id, response.next_cursor, token)
  } catch (reason) {
    if (token !== selectionToken || controller.signal.aborted) return
    eventsController = null
    detailError.value = getErrorMessage(reason)
    connectionState.value = 'connecting'
    connectToEvents(run.id, 0, token)
  }
}

function closeDetails() {
  stopEventConnection()
  selectedRun.value = null
  runEvents.value = []
  eventTotal.value = 0
  detailError.value = ''
  connectionState.value = 'idle'
}

function getStatus(state: RunState) {
  return statusInfo[state]
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '暂无'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

function eventMessage(event: RunEvent): string {
  for (const key of ['message', 'error', 'detail', 'text']) {
    const value = event.payload[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return JSON.stringify(event.payload) ?? '{}'
}

function eventUrl(event: RunEvent): string | null {
  for (const key of ['url', 'download_url', 'artifact_url', 'href']) {
    const value = event.payload[key]
    if (typeof value !== 'string' || !value.trim()) continue
    try {
      const url = new URL(value, window.location.origin)
      if (url.protocol === 'http:' || url.protocol === 'https:') return url.href
    } catch {
      continue
    }
  }
  return null
}

function eventLabel(event: RunEvent): string {
  for (const key of ['name', 'file_name', 'filename', 'title']) {
    const value = event.payload[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return event.type === 'screenshot' ? '查看截图' : '下载产物'
}

onMounted(refresh)
onBeforeUnmount(() => {
  activeController?.abort()
  stopEventConnection()
})
</script>

<template>
  <section class="execution" aria-labelledby="execution-title">
    <div class="page-heading">
      <div><span>EXECUTION CENTER</span><h2 id="execution-title">运行列表</h2><p>查看来自平台 API 的真实测试执行记录与服务状态。</p></div>
      <button class="primary-button" type="button" :disabled="serviceState === 'loading'" @click="refresh">
        <AppIcon name="refresh" />{{ serviceState === 'loading' ? '同步中' : '刷新数据' }}
      </button>
    </div>

    <div class="service-banner" :class="serviceState" role="status" aria-live="polite">
      <span class="state-dot" aria-hidden="true"></span>
      <div><strong>{{ serviceState === 'online' ? '服务在线' : serviceState === 'offline' ? '服务离线' : serviceState === 'degraded' ? '部分服务异常' : '正在连接服务' }}</strong><small>{{ serviceDetail }}</small></div>
      <code>/health/ready</code><code>/api/v1/runs</code>
    </div>

    <div class="runs-panel">
      <div class="toolbar">
        <div><h3>全部运行</h3><span v-if="!runsError">{{ runs.length }} 条记录</span></div>
        <div class="filters">
          <label class="search-box"><span class="sr-only">搜索运行</span><AppIcon name="search" /><input v-model="search" type="search" placeholder="搜索引擎或运行 ID" /></label>
          <label><span class="sr-only">筛选运行状态</span><select v-model="statusFilter"><option value="all">全部状态</option><option value="running">进行中</option><option value="success">成功</option><option value="failed">失败</option></select></label>
        </div>
      </div>

      <div v-if="serviceState === 'loading'" class="state-view" role="status">
        <span class="loader" aria-hidden="true"></span><strong>正在读取运行数据</strong><p>正在请求同源 API，请稍候。</p>
      </div>
      <div v-else-if="runsError" class="state-view offline-view">
        <span class="offline-icon" aria-hidden="true">!</span><strong>运行数据暂时不可用</strong><p>{{ runsError }}。请确认后端服务已启动且当前站点可访问同源接口。</p><button type="button" @click="refresh">重新连接</button>
      </div>
      <div v-else-if="runs.length === 0" class="state-view">
        <span class="empty-icon" aria-hidden="true">◇</span><strong>暂无运行记录</strong><p>API 已连接，但当前没有返回任何测试运行数据。</p>
      </div>
      <div v-else-if="filteredRuns.length === 0" class="state-view compact">
        <span class="empty-icon" aria-hidden="true">⌕</span><strong>没有匹配的运行</strong><p>请调整搜索关键词或状态筛选条件。</p>
      </div>
      <div v-else class="table-wrap" role="region" aria-label="测试运行列表，可横向滚动" tabindex="0">
        <table>
          <caption class="sr-only">测试运行列表</caption>
          <thead><tr><th scope="col">运行</th><th scope="col">状态</th><th scope="col">进度</th><th scope="col">触发方式</th><th scope="col">环境</th><th scope="col">创建时间</th><th scope="col">耗时</th></tr></thead>
          <tbody>
            <tr v-for="run in filteredRuns" :key="run.id" :class="{ selected: selectedRun?.id === run.id }" @click="selectRun(run)">
              <td><button class="run-link" type="button" aria-controls="run-live-detail" :aria-expanded="selectedRun?.id === run.id" @click.stop="selectRun(run)"><strong>{{ run.engine }} 测试运行</strong><small>{{ run.id }}</small></button></td>
              <td><span class="status-pill" :class="getStatus(run.state).tone"><i></i>{{ getStatus(run.state).label }}</span></td>
              <td><span v-if="selectedRun?.id === run.id && progress !== null">{{ Math.round(progress) }}%</span><span v-else class="unavailable">暂无</span></td>
              <td>API</td><td><span class="unavailable">待执行</span></td><td>{{ formatTime(run.created_at) }}</td><td><span class="unavailable">暂无</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <section v-if="selectedRun" id="run-live-detail" ref="detailPanel" class="live-detail" tabindex="-1" aria-labelledby="run-detail-title">
      <header class="detail-header">
        <div><span>LIVE EXECUTION</span><h3 id="run-detail-title">{{ selectedRun.engine }} 运行详情</h3><small>{{ selectedRun.id }}</small></div>
        <button type="button" aria-label="关闭运行详情" @click="closeDetails"><AppIcon name="close" /></button>
      </header>

      <div class="detail-summary">
        <div class="connection-status" :class="connectionState" role="status" aria-live="polite"><i></i><span><strong>{{ connectionInfo[connectionState].label }}</strong><small>{{ connectionInfo[connectionState].detail }}</small></span></div>
        <div class="detail-stat"><span>状态</span><strong>{{ getStatus(selectedRun.state).label }}</strong></div>
        <div class="detail-stat"><span>事件</span><strong>{{ eventTotal }}</strong></div>
        <div class="detail-stat"><span>进度</span><strong>{{ progress === null ? '暂无' : `${Math.round(progress)}%` }}</strong></div>
      </div>

      <div class="progress-track" :class="{ empty: progress === null }">
        <div><span>执行进度</span><strong>{{ progress === null ? '尚未上报' : `${Math.round(progress)}%` }}</strong></div>
        <progress :value="progress ?? 0" max="100">{{ progress ?? 0 }}%</progress>
      </div>
      <p v-if="detailError" class="detail-error" role="alert">{{ detailError }}</p>

      <div class="event-columns">
        <article class="event-card logs-card">
          <header><h4>实时日志</h4><span>{{ logEvents.length }}</span></header>
          <ul v-if="logEvents.length" class="event-list" aria-live="polite">
            <li v-for="event in logEvents" :key="event.event_id"><time :datetime="event.occurred_at">{{ formatTime(event.occurred_at) }}</time><pre>{{ eventMessage(event) }}</pre></li>
          </ul>
          <p v-else class="event-empty">暂无日志事件</p>
        </article>
        <article class="event-card">
          <header><h4>错误</h4><span>{{ errorEvents.length }}</span></header>
          <ul v-if="errorEvents.length" class="event-list error-list">
            <li v-for="event in errorEvents" :key="event.event_id"><time :datetime="event.occurred_at">{{ formatTime(event.occurred_at) }}</time><p>{{ eventMessage(event) }}</p></li>
          </ul>
          <p v-else class="event-empty">暂无错误事件</p>
        </article>
        <article class="event-card">
          <header><h4>截图与产物</h4><span>{{ assetEvents.length }}</span></header>
          <ul v-if="assetEvents.length" class="asset-list">
            <li v-for="event in assetEvents" :key="event.event_id"><span class="asset-kind">{{ event.type === 'screenshot' ? '截图' : '产物' }}</span><a v-if="eventUrl(event)" :href="eventUrl(event) ?? undefined" target="_blank" rel="noopener noreferrer">{{ eventLabel(event) }}</a><span v-else>{{ eventLabel(event) }}（无链接）</span></li>
          </ul>
          <p v-else class="event-empty">暂无截图或产物</p>
        </article>
      </div>
    </section>
  </section>
</template>

<style scoped>
.execution{display:grid;gap:20px}.page-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:20px}.page-heading>div>span{color:#6384dc;font-size:9px;font-weight:750;letter-spacing:.15em}.page-heading h2{margin:5px 0 5px;color:#1e293b;font-size:23px}.page-heading p{margin:0;color:#8d98a9;font-size:11px}.primary-button{height:38px;padding:0 15px;border:0;border-radius:8px;color:#fff;background:#5279df;display:flex;align-items:center;gap:7px;font:600 11px inherit;cursor:pointer;box-shadow:0 7px 15px rgba(82,121,223,.2)}.primary-button:disabled{cursor:wait;opacity:.7}.primary-button svg{width:15px;height:15px}
.service-banner{min-height:58px;padding:11px 15px;border:1px solid #dce8ff;border-radius:10px;background:#f5f8ff;display:flex;align-items:center;gap:11px;box-sizing:border-box}.service-banner .state-dot{width:9px;height:9px;border-radius:50%;background:#5d86ec;box-shadow:0 0 0 5px rgba(93,134,236,.12)}.service-banner>div{display:flex;flex:1;flex-direction:column}.service-banner strong{color:#395071;font-size:11px}.service-banner small{margin-top:2px;color:#7f8ca0;font-size:9px}.service-banner code{padding:4px 7px;border-radius:5px;color:#66758b;background:rgba(255,255,255,.7);font:9px ui-monospace,monospace}.service-banner.online{border-color:#d7eee6;background:#f2fbf8}.service-banner.online .state-dot{background:#26aa7a;box-shadow:0 0 0 5px rgba(38,170,122,.11)}.service-banner.offline{border-color:#f3dadd;background:#fff6f6}.service-banner.offline .state-dot{background:#db5c68;box-shadow:0 0 0 5px rgba(219,92,104,.1)}.service-banner.degraded{border-color:#f1e2bf;background:#fffaf0}.service-banner.degraded .state-dot{background:#e4a63f;box-shadow:0 0 0 5px rgba(228,166,63,.12)}
.runs-panel{min-height:405px;border:1px solid #e5eaf2;border-radius:12px;background:#fff;overflow:hidden}.toolbar{min-height:69px;padding:12px 18px;border-bottom:1px solid #edf0f5;display:flex;align-items:center;justify-content:space-between;gap:16px;box-sizing:border-box}.toolbar>div:first-child{display:flex;align-items:center;gap:9px}.toolbar h3{margin:0;color:#293448;font-size:13px}.toolbar>div:first-child span{padding:3px 6px;border-radius:5px;color:#7b8798;background:#f0f3f7;font-size:9px}.filters{display:flex;gap:8px}.search-box{height:34px;width:220px;padding:0 10px;border:1px solid #e4e9f0;border-radius:7px;background:#fafbfd;display:flex;align-items:center;gap:7px}.search-box svg{width:14px;color:#9aa5b5}.search-box input{min-width:0;flex:1;border:0;outline:0;color:#3b4659;background:transparent;font:10px inherit}.filters select{height:34px;padding:0 28px 0 10px;border:1px solid #e4e9f0;border-radius:7px;color:#647085;background:#fafbfd;font:10px inherit}
.state-view{min-height:334px;padding:30px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;box-sizing:border-box}.state-view>span:not(.loader){width:46px;height:46px;border-radius:14px;display:grid;place-items:center;color:#7588ac;background:#eff3f9;font-size:19px}.state-view strong{margin-top:13px;color:#435066;font-size:12px}.state-view p{max-width:390px;margin:5px 0 0;color:#9aa4b3;font-size:10px;line-height:1.6}.state-view button{margin-top:14px;padding:7px 13px;border:1px solid #d8e1f3;border-radius:7px;color:#5073cc;background:#f5f8ff;font:600 10px inherit;cursor:pointer}.state-view.compact{min-height:250px}.offline-view .offline-icon{color:#d45b67;background:#fff0f1}.loader{width:28px;height:28px;border:3px solid #e2e9f8;border-top-color:#5b80df;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
.table-wrap{overflow-x:auto}table{width:100%;min-width:850px;border-collapse:collapse;text-align:left}th{height:39px;padding:0 14px;color:#8e99aa;background:#fafbfd;font-size:9px;font-weight:650;letter-spacing:.03em}td{height:61px;padding:0 14px;border-top:1px solid #eff2f6;color:#5c687a;font-size:10px}tbody tr{cursor:pointer;transition:background .15s}tbody tr:hover,tbody tr.selected{background:#f7f9ff}.run-link{padding:0;border:0;background:transparent;text-align:left;cursor:pointer}.run-link:focus-visible{outline:2px solid #5279df;outline-offset:4px;border-radius:2px}.run-link strong{display:block;max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#344055;font:700 11px inherit}.run-link small{display:block;max-width:220px;margin-top:3px;overflow:hidden;text-overflow:ellipsis;color:#9ba4b3;font:8px inherit}.status-pill{display:inline-flex;align-items:center;gap:5px;padding:4px 7px;border-radius:20px;color:#69768a;background:#f0f3f6;font-size:9px}.status-pill i{width:5px;height:5px;border-radius:50%;background:currentColor}.status-pill.success{color:#29956f;background:#eaf8f3}.status-pill.running{color:#527bdd;background:#eaf0ff}.status-pill.pending{color:#bd842d;background:#fff4de}.status-pill.danger{color:#d35a66;background:#fff0f1}.unavailable{color:#8d98a9}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.live-detail{border:1px solid #e1e7f1;border-radius:12px;background:#fff;box-shadow:0 12px 35px rgba(51,66,94,.08);overflow:hidden;outline:none}.live-detail:focus-visible{box-shadow:0 0 0 3px rgba(82,121,223,.18),0 12px 35px rgba(51,66,94,.08)}.detail-header{padding:16px 18px;border-bottom:1px solid #edf0f5;display:flex;align-items:center;justify-content:space-between}.detail-header>div>span{color:#6384dc;font-size:8px;font-weight:750;letter-spacing:.15em}.detail-header h3{margin:4px 0 2px;color:#293448;font-size:14px}.detail-header small{color:#98a2b1;font-size:8px}.detail-header button{width:30px;height:30px;border:1px solid #e4e9f0;border-radius:7px;color:#78869a;background:#fafbfd;display:grid;place-items:center;cursor:pointer}.detail-header button svg{width:13px;height:13px}.detail-summary{padding:14px 18px;display:grid;grid-template-columns:minmax(180px,1.7fr) repeat(3,minmax(80px,1fr));gap:10px}.connection-status,.detail-stat{min-height:53px;padding:10px 12px;border:1px solid #e9edf3;border-radius:8px;background:#fafbfd;box-sizing:border-box}.connection-status{display:flex;align-items:center;gap:10px}.connection-status>i{width:8px;height:8px;border-radius:50%;background:#99a4b3}.connection-status>span{display:flex;flex-direction:column}.connection-status strong,.detail-stat strong{color:#3b485c;font-size:10px}.connection-status small{margin-top:2px;color:#929dac;font-size:8px}.connection-status.live>i{background:#28a879;box-shadow:0 0 0 4px rgba(40,168,121,.1)}.connection-status.loading>i,.connection-status.connecting>i,.connection-status.reconnecting>i{background:#e1a23b;box-shadow:0 0 0 4px rgba(225,162,59,.1)}.connection-status.error>i{background:#d95c68}.detail-stat{display:flex;flex-direction:column;justify-content:center}.detail-stat span{margin-bottom:3px;color:#939ead;font-size:8px}.progress-track{padding:0 18px 15px}.progress-track>div{margin-bottom:6px;display:flex;justify-content:space-between;color:#718095;font-size:9px}.progress-track strong{color:#4c5d76}.progress-track progress{width:100%;height:7px;border:0;border-radius:10px;overflow:hidden;background:#edf1f7}.progress-track progress::-webkit-progress-bar{background:#edf1f7}.progress-track progress::-webkit-progress-value{border-radius:10px;background:#5d83e5}.progress-track progress::-moz-progress-bar{border-radius:10px;background:#5d83e5}.progress-track.empty progress{opacity:.55}.detail-error{margin:0 18px 14px;padding:9px 11px;border-radius:7px;color:#b94a55;background:#fff1f2;font-size:9px}.event-columns{padding:0 18px 18px;display:grid;grid-template-columns:1.35fr 1fr 1fr;gap:12px}.event-card{min-width:0;min-height:170px;border:1px solid #e9edf3;border-radius:9px;overflow:hidden}.event-card>header{height:42px;padding:0 12px;border-bottom:1px solid #edf0f5;background:#fafbfd;display:flex;align-items:center;justify-content:space-between}.event-card h4{margin:0;color:#435066;font-size:10px}.event-card header span{padding:2px 5px;border-radius:4px;color:#7b8798;background:#edf1f6;font-size:8px}.event-list,.asset-list{max-height:240px;margin:0;padding:0;overflow:auto;list-style:none}.event-list li,.asset-list li{padding:9px 11px;border-bottom:1px solid #f0f2f6}.event-list li:last-child,.asset-list li:last-child{border-bottom:0}.event-list time{display:block;margin-bottom:4px;color:#9aa4b3;font-size:8px}.event-list pre,.event-list p{margin:0;color:#536176;font:9px/1.55 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}.error-list p{color:#b74e59}.asset-list li{display:flex;align-items:center;gap:7px;color:#69768a;font-size:9px}.asset-kind{flex:none;padding:3px 5px;border-radius:4px;color:#5c76b6;background:#edf2ff;font-size:8px}.asset-list a{color:#5279d7;font-weight:650;text-decoration:none}.asset-list a:hover{text-decoration:underline}.event-empty{margin:0;padding:38px 12px;color:#9ca6b4;text-align:center;font-size:9px}
@media(max-width:850px){.event-columns{grid-template-columns:1fr 1fr}.logs-card{grid-column:1/-1}.detail-summary{grid-template-columns:1fr 1fr}.connection-status{grid-column:1/-1}}
@media(max-width:700px){.page-heading{align-items:flex-start}.page-heading p{max-width:250px}.primary-button{padding:0 11px}.service-banner{align-items:flex-start;flex-wrap:wrap}.service-banner>div{flex-basis:calc(100% - 24px)}.service-banner code{margin-left:20px}.toolbar{align-items:flex-start;flex-direction:column}.filters{width:100%}.search-box{width:auto;flex:1}.runs-panel{min-height:380px}.state-view{min-height:280px}.event-columns{grid-template-columns:1fr}.logs-card{grid-column:auto}}
@media(max-width:480px){.page-heading{display:block}.primary-button{margin-top:14px}.filters{flex-direction:column}.filters label,.filters select{width:100%;box-sizing:border-box}.service-banner code{display:none}}
</style>