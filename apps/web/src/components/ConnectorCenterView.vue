<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import {
  createConnector, deleteConnector, fetchConnectors, getConnectorErrorMessage,
  setConnectorEnabled, testConnector, updateConnector,
} from '../services/connectors'
import type {
  Connector, ConnectorAuthType, ConnectorCreateInput, ConnectorKind, ConnectorUpdateInput,
} from '../types/connectors'

interface ConnectorDraft {
  source: Connector | null
  name: string
  description: string
  kind: ConnectorKind
  baseUrl: string
  authType: ConnectorAuthType
  authHeader: string
  credential: string
  timeoutSeconds: number
}
interface TestFeedback { success: boolean; message: string; detail: string }

const connectors = shallowRef<Connector[]>([])
const search = ref('')
const loading = ref(false)
const loadError = ref('')
const expandedIds = ref<string[]>([])
const testingIds = ref<string[]>([])
const togglingIds = ref<string[]>([])
const feedback = ref<Record<string, TestFeedback>>({})
const draft = ref<ConnectorDraft | null>(null)
const formError = ref('')
const saving = ref(false)
const deleteTarget = ref<Connector | null>(null)
const deleting = ref(false)
const deleteError = ref('')
const createButton = ref<HTMLButtonElement | null>(null)
const formNameInput = ref<HTMLInputElement | null>(null)
const confirmCancel = ref<HTMLButtonElement | null>(null)
let controller: AbortController | null = null
let modalTrigger: HTMLElement | null = null
let unmounted = false

const filteredConnectors = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  if (!query) return connectors.value
  return connectors.value.filter((item) =>
    [item.name, item.description, item.base_url, item.kind].some((value) =>
      value.toLocaleLowerCase().includes(query)),
  )
})
const enabledCount = computed(() => connectors.value.filter((item) => item.enabled).length)

function replaceConnector(updated: Connector) {
  connectors.value = connectors.value.map((item) => item.id === updated.id ? updated : item)
}

async function loadConnectors() {
  if (unmounted) return
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = ''
  try {
    const loaded = await fetchConnectors(current.signal)
    if (unmounted || controller !== current) return
    connectors.value = loaded
  } catch (reason) {
    if (!unmounted && controller === current
      && !(reason instanceof Error && reason.name === 'AbortError')) {
      loadError.value = getConnectorErrorMessage(reason)
    }
  } finally {
    if (!unmounted && controller === current) loading.value = false
  }
}

function blankDraft(): ConnectorDraft {
  return {
    source: null, name: '', description: '', kind: 'MCP', baseUrl: '',
    authType: 'NONE', authHeader: 'Authorization', credential: '', timeoutSeconds: 30,
  }
}

function rememberModalTrigger(event?: Event) {
  modalTrigger = event?.currentTarget instanceof HTMLElement
    ? event.currentTarget
    : document.activeElement instanceof HTMLElement ? document.activeElement : null
}

function restoreModalTrigger() {
  const trigger = modalTrigger
  modalTrigger = null
  void nextTick(() => {
    if (unmounted) return
    if (trigger?.isConnected) trigger.focus()
    else createButton.value?.focus()
  })
}

function openCreate(event?: Event) {
  rememberModalTrigger(event)
  draft.value = blankDraft()
  formError.value = ''
  void nextTick(() => formNameInput.value?.focus())
}

function openEdit(item: Connector, event?: Event) {
  rememberModalTrigger(event)
  draft.value = {
    source: item, name: item.name, description: item.description, kind: item.kind,
    baseUrl: item.base_url, authType: item.auth_type, authHeader: item.auth_header,
    credential: '', timeoutSeconds: item.timeout_seconds,
  }
  formError.value = ''
  void nextTick(() => formNameInput.value?.focus())
}

function closeDraft(force = false) {
  if (saving.value && !force) return
  draft.value = null
  restoreModalTrigger()
}

function openDelete(item: Connector, event?: Event) {
  rememberModalTrigger(event)
  deleteTarget.value = item
  deleteError.value = ''
  void nextTick(() => confirmCancel.value?.focus())
}

function closeDelete(force = false) {
  if (deleting.value && !force) return
  deleteTarget.value = null
  restoreModalTrigger()
}

function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab') return
  const container = event.currentTarget
  if (!(container instanceof HTMLElement)) return
  const selector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
  const focusable = Array.from(container.querySelectorAll<HTMLElement>(selector))
    .filter((item) => item.getAttribute('aria-hidden') !== 'true')
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (!first || !last) return
  const active = document.activeElement
  if (event.shiftKey && (active === first || !container.contains(active))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || !container.contains(active))) {
    event.preventDefault()
    first.focus()
  }
}

function changeAuthType() {
  if (!draft.value) return
  if (draft.value.authType === 'BEARER' && !draft.value.authHeader.trim()) {
    draft.value.authHeader = 'Authorization'
  }
  if (draft.value.authType === 'API_KEY'
    && (!draft.value.authHeader.trim() || draft.value.authHeader === 'Authorization')) {
    draft.value.authHeader = 'X-API-Key'
  }
}

function validHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch { return false }
}

function credentialRequired(value: ConnectorDraft): boolean {
  return value.authType !== 'NONE' && (!value.source || !value.source.credential_configured)
}

function buildInput(value: ConnectorDraft): ConnectorCreateInput {
  const name = value.name.trim()
  const baseUrl = value.baseUrl.trim()
  const authHeader = value.authHeader.trim()
  if (!name) throw new Error('连接器名称不能为空。')
  if (!baseUrl || !validHttpUrl(baseUrl)) throw new Error('服务地址必须是有效的 HTTP 或 HTTPS 地址。')
  if (value.authType !== 'NONE' && !authHeader) throw new Error('使用鉴权时，请填写鉴权请求头。')
  if (credentialRequired(value) && !value.credential.trim()) {
    throw new Error('当前鉴权方式需要凭据，请填写凭据。')
  }
  if (!Number.isInteger(value.timeoutSeconds)
    || value.timeoutSeconds < 1 || value.timeoutSeconds > 60) {
    throw new Error('超时时间必须是 1 到 60 秒之间的整数。')
  }
  return {
    name, description: value.description.trim(), kind: value.kind, base_url: baseUrl,
    auth_type: value.authType, auth_header: authHeader,
    ...(value.authType !== 'NONE' && value.credential.trim()
      ? { credential: value.credential.trim() } : {}),
    timeout_seconds: value.timeoutSeconds,
  }
}

async function submitForm() {
  const value = draft.value
  if (!value || saving.value) return
  formError.value = ''
  try {
    const input = buildInput(value)
    saving.value = true
    if (value.source) {
      const update: ConnectorUpdateInput = { ...input, state_version: value.source.state_version }
      const updated = await updateConnector(value.source.id, update)
      if (unmounted) return
      replaceConnector(updated)
    } else {
      const created = await createConnector(input)
      if (unmounted) return
      connectors.value = [...connectors.value, created]
    }
    closeDraft(true)
  } catch (reason) {
    if (!unmounted) formError.value = getConnectorErrorMessage(reason)
  } finally {
    if (!unmounted) saving.value = false
  }
}

async function toggleConnector(item: Connector) {
  if (togglingIds.value.includes(item.id)) return
  togglingIds.value = [...togglingIds.value, item.id]
  try {
    const updated = await setConnectorEnabled(item.id, !item.enabled, item.state_version)
    if (unmounted) return
    replaceConnector(updated)
  } catch (reason) {
    if (unmounted) return
    feedback.value = { ...feedback.value, [item.id]: {
      success: false, message: getConnectorErrorMessage(reason), detail: '',
    } }
    await loadConnectors()
  } finally {
    if (!unmounted) togglingIds.value = togglingIds.value.filter((id) => id !== item.id)
  }
}

async function runTest(item: Connector) {
  if (testingIds.value.includes(item.id)) return
  testingIds.value = [...testingIds.value, item.id]
  feedback.value = Object.fromEntries(Object.entries(feedback.value).filter(([id]) => id !== item.id))
  try {
    const result = await testConnector(item.id)
    if (unmounted) return
    replaceConnector(result.connector)
    feedback.value = { ...feedback.value, [item.id]: {
      success: result.success, message: result.message,
      detail: `${Math.round(result.latency_ms)} ms · ${result.tool_count} 个工具`,
    } }
  } catch (reason) {
    if (unmounted) return
    feedback.value = { ...feedback.value, [item.id]: {
      success: false, message: getConnectorErrorMessage(reason), detail: '',
    } }
  } finally {
    if (!unmounted) testingIds.value = testingIds.value.filter((id) => id !== item.id)
  }
}

async function confirmDelete() {
  const target = deleteTarget.value
  if (!target || deleting.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await deleteConnector(target.id)
    if (unmounted) return
    connectors.value = connectors.value.filter((item) => item.id !== target.id)
    expandedIds.value = expandedIds.value.filter((id) => id !== target.id)
    feedback.value = Object.fromEntries(
      Object.entries(feedback.value).filter(([id]) => id !== target.id),
    )
    closeDelete(true)
  } catch (reason) {
    if (!unmounted) deleteError.value = getConnectorErrorMessage(reason)
  } finally {
    if (!unmounted) deleting.value = false
  }
}

function toggleExpanded(id: string) {
  expandedIds.value = expandedIds.value.includes(id)
    ? expandedIds.value.filter((item) => item !== id) : [...expandedIds.value, id]
}

function formatDate(value: string | null): string {
  if (!value) return '尚未测试'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

function schemaSummary(schema: Record<string, unknown>): string {
  const properties = schema.properties
  if (properties && typeof properties === 'object' && !Array.isArray(properties)) {
    const names = Object.keys(properties)
    if (names.length) return `参数：${names.slice(0, 6).join('、')}${names.length > 6 ? '…' : ''}`
  }
  return '无需额外参数说明'
}

onMounted(() => void loadConnectors())
onBeforeUnmount(() => {
  unmounted = true
  controller?.abort()
})
</script>

<template>
  <section class="connector-center" aria-labelledby="connector-title">
    <header class="page-heading">
      <div>
        <p class="eyebrow">PLATFORM INTEGRATIONS</p>
        <h2 id="connector-title">连接器管理</h2>
        <p>统一配置外部能力，让测试流程安全、稳定地连接 MCP 与 HTTP 服务。</p>
      </div>
      <button ref="createButton" class="primary-button" type="button" @click="openCreate">
        <span aria-hidden="true">＋</span> 配置新连接器
      </button>
    </header>

    <div class="global-notice" role="note">
      <span aria-hidden="true">i</span>
      <p><strong>平台共享配置</strong>连接器对整个平台可用，不隶属于任何项目，请谨慎管理凭据和启用状态。</p>
    </div>

    <div class="connector-panel">
      <div class="panel-toolbar">
        <label class="search-box">
          <span aria-hidden="true">⌕</span><span class="sr-only">搜索连接器</span>
          <input v-model="search" type="search" placeholder="搜索名称、描述或地址" maxlength="200">
        </label>
        <div class="summary" aria-live="polite">
          <span><strong>{{ connectors.length }}</strong> 个连接器</span>
          <span class="summary-divider"></span>
          <span><i></i><strong>{{ enabledCount }}</strong> 个已启用</span>
        </div>
      </div>

      <div v-if="loading" class="state-card" role="status" aria-live="polite">
        <span class="spinner"></span><strong>正在加载连接器</strong><p>请稍候…</p>
      </div>
      <div v-else-if="loadError" class="state-card error-state" role="alert">
        <span class="state-icon">!</span><strong>连接器加载失败</strong><p>{{ loadError }}</p>
        <button type="button" @click="loadConnectors">重新加载</button>
      </div>
      <div v-else-if="filteredConnectors.length === 0" class="state-card empty-state">
        <span class="state-icon">◇</span>
        <strong>{{ connectors.length ? '没有匹配的连接器' : '还没有连接器' }}</strong>
        <p>{{ connectors.length ? '请尝试更换搜索关键词。' : '配置首个连接器，将外部工具接入测试平台。' }}</p>
        <button v-if="!connectors.length" type="button" @click="openCreate">配置连接器</button>
      </div>

      <div v-else class="connector-list">
        <article v-for="item in filteredConnectors" :key="item.id" class="connector-row">
          <div class="row-main">
            <button class="expand-button" type="button"
              :aria-expanded="expandedIds.includes(item.id)" :aria-controls="`details-${item.id}`"
              :aria-label="`${expandedIds.includes(item.id) ? '收起' : '展开'} ${item.name}`"
              @click="toggleExpanded(item.id)">
              <span aria-hidden="true" :class="{ expanded: expandedIds.includes(item.id) }">›</span>
            </button>
            <div class="connector-avatar" :class="item.kind.toLowerCase()" aria-hidden="true">
              {{ item.name.trim().charAt(0).toLocaleUpperCase() || 'C' }}
            </div>
            <div class="connector-copy">
              <div class="name-line">
                <h3>{{ item.name }}</h3><span class="kind-badge">{{ item.kind }}</span>
                <span class="status-copy"><i :class="{ on: item.enabled }"></i>{{ item.enabled ? '已启用' : '已停用' }}</span>
              </div>
              <p>{{ item.description || '暂无描述' }}</p>
              <div class="connector-meta">
                <span class="url" :title="item.base_url">{{ item.base_url }}</span>
                <span>{{ item.kind === 'MCP' ? `${item.tools.length} 个工具` : (item.last_test_success === true ? 'HTTP 连接正常' : item.last_test_success === false ? 'HTTP 连接异常' : 'HTTP 待测试') }}</span>
                <span>{{ item.auth_type === 'NONE' ? '无需鉴权' : item.credential_configured ? '凭据已保存' : '凭据未配置' }}</span>
              </div>
            </div>
            <div class="row-controls">
              <button class="switch" :class="{ checked: item.enabled }" type="button" role="switch"
                :aria-checked="item.enabled" :aria-label="`${item.enabled ? '停用' : '启用'} ${item.name}`"
                :disabled="togglingIds.includes(item.id)" @click="toggleConnector(item)"><span></span></button>
              <div class="row-actions">
                <button type="button" :disabled="testingIds.includes(item.id)" @click="runTest(item)">
                  {{ testingIds.includes(item.id) ? '测试中…' : '测试连接' }}
                </button>
                <button type="button" @click="openEdit(item, $event)">编辑</button>
                <button class="danger-link" type="button" @click="openDelete(item, $event)">删除</button>
              </div>
            </div>
          </div>

          <div v-if="feedback[item.id]" class="test-feedback" :class="{ success: feedback[item.id]?.success }" role="status">
            <span aria-hidden="true">{{ feedback[item.id]?.success ? '✓' : '!' }}</span>
            <p><strong>{{ feedback[item.id]?.success ? '连接成功' : '操作未完成' }}</strong>{{ feedback[item.id]?.message }}</p>
            <small v-if="feedback[item.id]?.detail">{{ feedback[item.id]?.detail }}</small>
          </div>

          <div v-show="expandedIds.includes(item.id)" :id="`details-${item.id}`" class="row-details">
            <div class="details-heading">
              <h4>{{ item.kind === 'MCP' ? `可用工具（${item.tools.length}）` : '连接详情' }}</h4>
              <span>超时 {{ item.timeout_seconds }} 秒</span>
            </div>
            <div v-if="item.kind === 'MCP' && item.tools.length" class="tool-grid">
              <div v-for="tool in item.tools" :key="tool.name" class="tool-card">
                <span aria-hidden="true">⌘</span><div><strong>{{ tool.name }}</strong>
                  <p>{{ tool.description || '暂无工具描述' }}</p><small>{{ schemaSummary(tool.input_schema) }}</small>
                </div>
              </div>
            </div>
            <p v-else-if="item.kind === 'MCP'" class="no-tools">尚未发现工具，请运行“测试连接”同步工具列表。</p>
            <dl class="test-meta">
              <div><dt>最近测试</dt><dd>{{ formatDate(item.last_tested_at) }}</dd></div>
              <div><dt>测试结果</dt><dd :class="{ good: item.last_test_success === true, bad: item.last_test_success === false }">
                {{ item.last_test_success === null ? '暂无结果' : item.last_test_success ? '连接成功' : '连接失败' }}
              </dd></div>
              <div v-if="item.last_error"><dt>错误信息</dt><dd class="bad">{{ item.last_error }}</dd></div>
            </dl>
          </div>
        </article>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="draft" class="modal-layer" @click.self="closeDraft()">
        <form class="connector-modal" role="dialog" aria-modal="true"
          aria-labelledby="connector-form-title" @keydown="trapFocus" @submit.prevent="submitForm">
          <div class="modal-header"><div><p>{{ draft.source ? 'EDIT CONNECTOR' : 'NEW CONNECTOR' }}</p>
            <h2 id="connector-form-title">{{ draft.source ? '编辑连接器' : '配置新连接器' }}</h2></div>
            <button type="button" aria-label="关闭" :disabled="saving" @click="closeDraft()">×</button>
          </div>
          <div class="modal-body">
            <label class="field"><span>名称 <b>*</b></span><input ref="formNameInput" v-model="draft.name" required maxlength="255" placeholder="例如：研发知识库"></label>
            <label class="field"><span>描述</span><textarea v-model="draft.description" maxlength="2000" rows="3" placeholder="说明连接器的用途和范围"></textarea></label>
            <div class="field-pair">
              <label class="field"><span>连接类型 <b>*</b></span><select v-model="draft.kind"><option value="MCP">MCP</option><option value="HTTP">HTTP</option></select></label>
              <label class="field"><span>超时时间（秒）<b>*</b></span><input v-model.number="draft.timeoutSeconds" type="number" required min="1" max="60" step="1"></label>
            </div>
            <label class="field"><span>服务地址 <b>*</b></span><input v-model="draft.baseUrl" type="url" required maxlength="2048" placeholder="https://example.com/mcp"></label>
            <label class="field"><span>鉴权方式 <b>*</b></span><select v-model="draft.authType" @change="changeAuthType"><option value="NONE">无需鉴权</option><option value="BEARER">Bearer Token</option><option value="API_KEY">API Key</option></select></label>
            <template v-if="draft.authType !== 'NONE'">
              <label class="field"><span>鉴权请求头 <b>*</b></span><input v-model="draft.authHeader" required maxlength="255" placeholder="Authorization"></label>
              <label class="field"><span>凭据 <b v-if="credentialRequired(draft)">*</b><em v-if="draft.source?.credential_configured">已保存</em></span>
                <input v-model="draft.credential" type="password" :required="credentialRequired(draft)"
                  maxlength="65536" autocomplete="new-password"
                  :placeholder="draft.source?.credential_configured ? '留空以保留已保存的凭据' : '输入 Token 或 API Key'">
                <small v-if="draft.source?.credential_configured">平台不会返回凭据明文；留空不会覆盖现有凭据。</small>
              </label>
            </template>
            <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
          </div>
          <div class="modal-actions"><button type="button" :disabled="saving" @click="closeDraft()">取消</button>
            <button class="save-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存连接器' }}</button></div>
        </form>
      </div>

      <div v-if="deleteTarget" class="modal-layer confirm-layer" @click.self="closeDelete()">
        <div class="confirm-dialog" role="alertdialog" aria-modal="true"
          aria-labelledby="delete-title" @keydown="trapFocus">
          <span class="warning-icon" aria-hidden="true">!</span><h2 id="delete-title">删除连接器</h2>
          <p>确认删除“<strong>{{ deleteTarget.name }}</strong>”吗？使用该连接器的流程将无法继续调用，且此操作不可撤销。</p>
          <p v-if="deleteError" class="form-error" role="alert">{{ deleteError }}</p>
          <div><button ref="confirmCancel" type="button" :disabled="deleting" @click="closeDelete()">取消</button>
            <button class="delete-button" type="button" :disabled="deleting" @click="confirmDelete">{{ deleting ? '删除中…' : '确认删除' }}</button></div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.connector-center{color:#273247}.page-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:20px}.eyebrow{margin:0 0 7px!important;color:#6a7fa9!important;font-size:11px!important;font-weight:750;letter-spacing:.14em}.page-heading h2{margin:0;color:#172238;font-size:27px;line-height:1.25}.page-heading p{margin:8px 0 0;color:#7a8699;font-size:14px;line-height:1.6}.primary-button{min-height:44px;padding:0 18px;border:0;border-radius:10px;color:#fff;background:linear-gradient(110deg,#426fd6,#6d61db);box-shadow:0 8px 20px rgba(75,99,205,.22);font-size:14px;font-weight:700;cursor:pointer}.primary-button span{font-size:19px;vertical-align:-1px}.global-notice{display:flex;align-items:center;gap:12px;margin-bottom:18px;padding:13px 16px;border:1px solid #dce7fb;border-radius:11px;background:#f5f8ff}.global-notice>span{flex:0 0 auto;width:24px;height:24px;display:grid;place-items:center;border-radius:50%;color:#4169c6;background:#e2eaff;font-size:13px;font-weight:800}.global-notice p{margin:0;color:#627089;font-size:13px;line-height:1.5}.global-notice strong{margin-right:9px;color:#35496d}
.connector-panel{overflow:hidden;border:1px solid #e4e9f1;border-radius:14px;background:#fff;box-shadow:0 3px 14px rgba(29,43,74,.04)}.panel-toolbar{min-height:72px;padding:14px 20px;display:flex;align-items:center;justify-content:space-between;gap:18px;border-bottom:1px solid #edf0f5}.search-box{width:min(390px,52%);height:42px;display:flex;align-items:center;gap:9px;padding:0 13px;border:1px solid #dfe5ed;border-radius:9px;color:#8a96a9;background:#fafbfd}.search-box:focus-within{border-color:#6b8fe1;box-shadow:0 0 0 3px #edf2ff}.search-box input{width:100%;height:100%;padding:0;border:0;outline:0;color:#344057;background:transparent;font-size:13px}.summary{display:flex;align-items:center;gap:10px;color:#8490a3;font-size:12px}.summary>span{display:flex;align-items:center;gap:5px}.summary strong{color:#354258;font-size:14px}.summary i{width:8px;height:8px;border-radius:50%;background:#24ad72;box-shadow:0 0 0 3px #e2f7ee}.summary-divider{width:1px;height:18px;background:#e3e8ef}
.connector-list{padding:0 20px 18px}.connector-row{border-bottom:1px solid #e9edf3}.connector-row:last-child{border-bottom:0}.row-main{min-height:126px;display:flex;align-items:center;gap:15px;padding:20px 4px}.expand-button{flex:0 0 auto;width:38px;height:38px;border:0;border-radius:8px;color:#7f8a9d;background:transparent;font-size:29px;cursor:pointer}.expand-button:hover{background:#f3f6fa}.expand-button span{display:block;transform:rotate(0);transition:transform .18s}.expand-button span.expanded{transform:rotate(90deg)}.connector-avatar{flex:0 0 auto;width:48px;height:48px;display:grid;place-items:center;border-radius:13px;color:#4e6ab8;background:linear-gradient(145deg,#e1e9ff,#f0f4ff);font-size:19px;font-weight:800}.connector-avatar.http{color:#8a5bb2;background:linear-gradient(145deg,#f0e4ff,#faf4ff)}.connector-copy{min-width:0;flex:1}.name-line{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.name-line h3{margin:0;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#202c42;font-size:16px}.kind-badge{padding:3px 7px;border-radius:5px;color:#526aa8;background:#edf2ff;font-size:10px;font-weight:800;letter-spacing:.05em}.status-copy{display:flex;align-items:center;gap:5px;color:#8a95a6;font-size:11px}.status-copy i{width:7px;height:7px;border-radius:50%;background:#aeb7c4}.status-copy i.on{background:#25b476}.connector-copy>p{margin:7px 0;color:#707c90;font-size:13px;line-height:1.45}.connector-meta{display:flex;align-items:center;gap:9px;color:#939dac;font-size:11px}.connector-meta span+span{padding-left:9px;border-left:1px solid #dfe4eb}.connector-meta .url{max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.row-controls{flex:0 0 auto;display:flex;align-items:center;gap:20px}.switch{position:relative;width:52px;height:30px;padding:0;border:0;border-radius:16px;background:#cbd2dc;cursor:pointer;transition:background .2s}.switch span{position:absolute;top:4px;left:4px;width:22px;height:22px;border-radius:50%;background:#fff;box-shadow:0 2px 5px rgba(28,39,57,.24);transition:transform .2s}.switch.checked{background:#31b77d}.switch.checked span{transform:translateX(22px)}.switch:disabled{cursor:wait;opacity:.55}.row-actions{display:flex;gap:3px}.row-actions button{min-height:38px;padding:0 10px;border:0;border-radius:7px;color:#59709e;background:transparent;font-size:12px;font-weight:650;cursor:pointer}.row-actions button:hover:not(:disabled){background:#f2f5fb}.row-actions button.danger-link{color:#c05a64}.row-actions button:disabled{cursor:wait;opacity:.55}
.test-feedback{margin:-7px 4px 15px 102px;padding:10px 12px;display:flex;align-items:center;gap:9px;border:1px solid #f1ced2;border-radius:8px;color:#a24652;background:#fff7f8;font-size:12px}.test-feedback.success{border-color:#ccebdc;color:#217956;background:#f4fcf8}.test-feedback>span{width:20px;height:20px;display:grid;place-items:center;border-radius:50%;color:#fff;background:#ce6470;font-weight:800}.test-feedback.success>span{background:#36ad7c}.test-feedback p{flex:1;margin:0}.test-feedback strong{margin-right:7px}.test-feedback small{color:inherit;white-space:nowrap}.row-details{margin:0 4px 18px 102px;padding:17px 18px;border:1px solid #e4e9f1;border-radius:10px;background:#f9fafc}.details-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.details-heading h4{margin:0;color:#354157;font-size:13px}.details-heading>span{color:#8b96a7;font-size:11px}.tool-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.tool-card{min-width:0;padding:12px;display:flex;gap:10px;border:1px solid #e3e8f0;border-radius:8px;background:#fff}.tool-card>span{flex:0 0 auto;width:28px;height:28px;display:grid;place-items:center;border-radius:7px;color:#5772bd;background:#edf2ff}.tool-card>div{min-width:0}.tool-card strong{color:#354158;font-size:12px}.tool-card p{margin:4px 0;color:#7b8799;font-size:11px;line-height:1.45}.tool-card small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#9aa3b1;font-size:10px}.no-tools{margin:0;padding:12px;border-radius:7px;color:#7d899c;background:#fff;font-size:12px}.test-meta{display:flex;flex-wrap:wrap;gap:24px;margin:14px 0 0;padding-top:12px;border-top:1px solid #e4e9f0}.test-meta div{display:flex;gap:8px}.test-meta dt{color:#929dad;font-size:11px}.test-meta dd{margin:0;max-width:500px;color:#556176;font-size:11px}.test-meta dd.good{color:#218259}.test-meta dd.bad{color:#b44e59}
.state-card{min-height:310px;padding:48px 20px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}.state-card strong{margin-top:14px;color:#364258;font-size:15px}.state-card p{margin:7px 0 0;color:#8a95a7;font-size:13px}.state-card button{min-height:38px;margin-top:16px;padding:0 15px;border:1px solid #d9e0eb;border-radius:8px;color:#4d68a8;background:#fff;font-weight:650;cursor:pointer}.state-icon,.spinner{width:42px;height:42px;display:grid;place-items:center;border-radius:12px;color:#667fbf;background:#edf2ff;font-size:20px;font-weight:800}.spinner{border:4px solid #e4ebfa;border-top-color:#5478d2;border-radius:50%;background:transparent;animation:spin .8s linear infinite}.error-state .state-icon{color:#bd5360;background:#fff0f2}@keyframes spin{to{transform:rotate(360deg)}}
.modal-layer{position:fixed;inset:0;z-index:80;padding:24px;display:flex;align-items:center;justify-content:center;background:rgba(16,24,40,.46);backdrop-filter:blur(2px)}.connector-modal{width:min(680px,100%);max-height:calc(100vh - 48px);display:flex;overflow:hidden;flex-direction:column;border-radius:16px;background:#fff;box-shadow:0 24px 70px rgba(18,28,48,.3);animation:modal-in .18s ease-out}.modal-header{min-height:82px;padding:18px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e8ecf2}.modal-header p{margin:0 0 4px;color:#7186b0;font-size:10px;font-weight:750;letter-spacing:.13em}.modal-header h2{margin:0;color:#202c42;font-size:20px}.modal-header>button{width:40px;height:40px;border:0;border-radius:9px;color:#758095;background:#f5f7fa;font-size:25px;cursor:pointer}.modal-body{flex:1;overflow:auto;padding:22px 24px}.field{display:grid;gap:7px;margin-bottom:17px;color:#526075;font-size:13px;font-weight:650}.field span{display:flex;align-items:center;gap:6px}.field b{color:#df5665}.field em{margin-left:auto;padding:3px 7px;border-radius:10px;color:#247958;background:#e7f7ef;font-size:10px;font-style:normal}.field input,.field select,.field textarea{width:100%;min-height:44px;padding:10px 12px;border:1px solid #dce2eb;border-radius:8px;outline:0;color:#303c52;background:#fbfcfe;font:400 13px inherit;resize:vertical}.field input:focus,.field select:focus,.field textarea:focus{border-color:#6689dc;box-shadow:0 0 0 3px #edf2ff}.field small{color:#8c97a8;font-size:11px;font-weight:400;line-height:1.5}.field-pair{display:grid;grid-template-columns:1fr 1fr;gap:13px}.form-error{margin:4px 0 0;padding:10px 12px;border:1px solid #f3ccd1;border-radius:8px;color:#ae4653;background:#fff6f7;font-size:12px;line-height:1.5}.modal-actions{padding:16px 24px;display:flex;justify-content:flex-end;gap:10px;border-top:1px solid #e7ebf1;background:#fff}.modal-actions button,.confirm-dialog button{min-width:84px;min-height:42px;padding:0 15px;border:1px solid #dce2eb;border-radius:8px;color:#657187;background:#fff;font-size:13px;font-weight:700;cursor:pointer}.modal-actions .save-button{min-width:120px;border:0;color:#fff;background:#4e70cf}.modal-actions button:disabled,.confirm-dialog button:disabled{cursor:wait;opacity:.6}.confirm-layer{align-items:center;justify-content:center;padding:18px}.confirm-dialog{width:min(430px,100%);padding:28px;border-radius:14px;background:#fff;box-shadow:0 20px 55px rgba(18,28,48,.25);text-align:center}.warning-icon{width:44px;height:44px;margin:0 auto;display:grid;place-items:center;border-radius:50%;color:#c04c58;background:#ffebed;font-size:20px;font-weight:800}.confirm-dialog h2{margin:14px 0 8px;color:#263248;font-size:19px}.confirm-dialog>p{margin:0 0 17px;color:#6e798c;font-size:13px;line-height:1.7}.confirm-dialog>div{display:flex;justify-content:center;gap:10px}.confirm-dialog .delete-button{border:0;color:#fff;background:#d45663}@keyframes modal-in{from{opacity:0;transform:translateY(12px) scale(.98)}}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:1050px){.row-main{align-items:flex-start;flex-wrap:wrap}.connector-copy{min-width:calc(100% - 116px)}.row-controls{width:100%;padding-left:101px;justify-content:space-between}.test-feedback,.row-details{margin-left:101px}}
@media(max-width:650px){.page-heading{align-items:stretch;flex-direction:column}.primary-button{align-self:flex-start}.global-notice{align-items:flex-start}.panel-toolbar{align-items:stretch;flex-direction:column}.search-box{width:100%}.summary{justify-content:space-between}.connector-list{padding:0 14px 14px}.row-main{gap:10px;padding:17px 0}.expand-button{width:32px}.connector-avatar{width:42px;height:42px}.connector-copy{min-width:calc(100% - 94px)}.connector-meta{align-items:flex-start;flex-direction:column;gap:4px}.connector-meta span+span{padding-left:0;border-left:0}.connector-meta .url{max-width:100%}.row-controls{padding-left:0;gap:8px}.row-actions{flex-wrap:wrap;justify-content:flex-end}.test-feedback,.row-details{margin-left:0}.tool-grid{grid-template-columns:1fr}.test-meta{gap:10px;flex-direction:column}.field-pair{grid-template-columns:1fr}.modal-layer{padding:12px}.connector-modal{max-height:calc(100vh - 24px);border-radius:13px}.modal-header,.modal-body,.modal-actions{padding-left:18px;padding-right:18px}}
</style>
