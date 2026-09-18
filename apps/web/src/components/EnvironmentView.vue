<script setup lang="ts">
import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import {
  createEnvironment, fetchEnvironments, getEnvironmentErrorMessage, updateEnvironment,
} from '../services/environments'
import type {
  ConfigurationJson, ConfigurationValueWrite, Environment, EnvironmentCreateInput,
} from '../types/environment'

const props = defineProps<{ projectId: string; projectsLoading: boolean; projectsError: string }>()
type AuthMode = 'DIRECT_TOKEN' | 'LOGIN'
type SecretDraft = { value: string; ref: string }
type StoredValue =
  | { name: string; secret: true; secret_ref: string }
  | { name: string; secret: false; value: unknown }
interface Draft {
  source: Environment | null
  name: string
  baseUrl: string
  authMode: AuthMode
  directToken: SecretDraft
  loginUrl: string
  account: SecretDraft
  password: SecretDraft
  accountField: string
  passwordField: string
  tokenPath: string
  authHeader: string
  authPrefix: string
  preservedVariables: StoredValue[]
  commonParameters: StoredValue[]
}

const knownKeys = new Set([
  'BASE_URL', 'AUTH_MODE', 'LOGIN_URL', 'ACCOUNT_FIELD', 'PASSWORD_FIELD', 'TOKEN_PATH',
  'AUTH_HEADER', 'AUTH_PREFIX', 'AUTH_TOKEN', 'AUTH_ACCOUNT', 'AUTH_PASSWORD',
])
const environments = shallowRef<Environment[]>([])
const draft = ref<Draft | null>(null)
const loading = ref(false)
const saving = ref(false)
const dirty = ref(false)
const error = ref('')
const success = ref('')
let controller: AbortController | null = null

function publicText(item: Environment, name: string, fallback = ''): string {
  const value = item.environment_variables.find((entry) => entry.name === name)
  return value && !value.secret && typeof value.value === 'string' ? value.value : fallback
}

function secretDraft(item: Environment, name: string): SecretDraft {
  const value = item.environment_variables.find((entry) => entry.name === name)
  return { value: '', ref: value?.secret ? value.secret_ref : '' }
}

function fromEnvironment(item: Environment): Draft {
  return {
    source: item, name: item.name, baseUrl: publicText(item, 'BASE_URL'),
    authMode: publicText(item, 'AUTH_MODE') === 'LOGIN' ? 'LOGIN' : 'DIRECT_TOKEN',
    directToken: secretDraft(item, 'AUTH_TOKEN'), loginUrl: publicText(item, 'LOGIN_URL'),
    account: secretDraft(item, 'AUTH_ACCOUNT'), password: secretDraft(item, 'AUTH_PASSWORD'),
    accountField: publicText(item, 'ACCOUNT_FIELD', 'username'),
    passwordField: publicText(item, 'PASSWORD_FIELD', 'password'),
    tokenPath: publicText(item, 'TOKEN_PATH', 'data.token'),
    authHeader: publicText(item, 'AUTH_HEADER', 'Authorization'),
    authPrefix: publicText(item, 'AUTH_PREFIX', 'Bearer'),
    preservedVariables: item.environment_variables.filter((entry) => !knownKeys.has(entry.name)),
    commonParameters: item.common_parameters,
  }
}

function blankDraft(): Draft {
  return {
    source: null, name: '', baseUrl: '', authMode: 'DIRECT_TOKEN',
    directToken: { value: '', ref: '' }, loginUrl: '', account: { value: '', ref: '' },
    password: { value: '', ref: '' }, accountField: 'username', passwordField: 'password',
    tokenPath: 'data.token', authHeader: 'Authorization', authPrefix: 'Bearer',
    preservedVariables: [], commonParameters: [],
  }
}

function markDirty() {
  dirty.value = true
  success.value = ''
}

function choose(item: Environment) {
  draft.value = fromEnvironment(item)
  dirty.value = false
  error.value = ''
  success.value = ''
}

function createDraft() {
  draft.value = blankDraft()
  dirty.value = true
  error.value = ''
  success.value = ''
}

async function loadEnvironments() {
  if (!props.projectId) { environments.value = []; draft.value = null; return }
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  error.value = ''
  try {
    const items = await fetchEnvironments(props.projectId, current.signal)
    environments.value = items
    const selected = items.find((item) => item.id === draft.value?.source?.id) ?? items[0]
    draft.value = selected ? fromEnvironment(selected) : null
    dirty.value = false
  } catch (reason) {
    if (!(reason instanceof Error && reason.name === 'AbortError')) error.value = getEnvironmentErrorMessage(reason)
  } finally {
    if (controller === current) loading.value = false
  }
}

function validHttpUrl(value: string, allowRelative = false): boolean {
  if (allowRelative && value.startsWith('/') && !value.startsWith('//')) return true
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch { return false }
}

function responseWrites(values: StoredValue[]): ConfigurationValueWrite[] {
  return values.map((item): ConfigurationValueWrite => item.secret
    ? { name: item.name, secret: true, secret_ref: item.secret_ref }
    : { name: item.name, value: item.value as ConfigurationJson })
}

function appendSecret(values: ConfigurationValueWrite[], name: string, secret: SecretDraft) {
  if (secret.value) values.push({ name, value: secret.value, secret: true })
  else if (secret.ref) values.push({ name, secret: true, secret_ref: secret.ref })
  else throw new Error(`${name === 'AUTH_TOKEN' ? '直接 Token' : name === 'AUTH_ACCOUNT' ? '登录账号' : '登录密码'}不能为空。`)
}

function buildInput(): EnvironmentCreateInput {
  const value = draft.value
  if (!value) throw new Error('请先新建或选择环境。')
  if (!value.name.trim()) throw new Error('环境名称不能为空。')
  if (!validHttpUrl(value.baseUrl.trim())) throw new Error('基础域名必须是有效的 HTTP 或 HTTPS 地址。')
  const required = [value.accountField, value.passwordField, value.tokenPath, value.authHeader]
  if (required.some((item) => !item.trim())) throw new Error('高级配置项不能为空。')
  const variables: ConfigurationValueWrite[] = responseWrites(value.preservedVariables)
  variables.push(
    { name: 'BASE_URL', value: value.baseUrl.trim() },
    { name: 'AUTH_MODE', value: value.authMode },
    { name: 'ACCOUNT_FIELD', value: value.accountField.trim() },
    { name: 'PASSWORD_FIELD', value: value.passwordField.trim() },
    { name: 'TOKEN_PATH', value: value.tokenPath.trim() },
    { name: 'AUTH_HEADER', value: value.authHeader.trim() },
    { name: 'AUTH_PREFIX', value: value.authPrefix.trim() },
  )
  if (value.authMode === 'DIRECT_TOKEN') appendSecret(variables, 'AUTH_TOKEN', value.directToken)
  else {
    if (!value.loginUrl.trim() || !validHttpUrl(value.loginUrl.trim(), true)) {
      throw new Error('登录地址必须是有效的 HTTP(S) 地址或以 / 开头的相对路径。')
    }
    variables.push({ name: 'LOGIN_URL', value: value.loginUrl.trim() })
    appendSecret(variables, 'AUTH_ACCOUNT', value.account)
    appendSecret(variables, 'AUTH_PASSWORD', value.password)
  }
  return {
    project_id: props.projectId, name: value.name.trim(), environment_variables: variables,
    common_parameters: responseWrites(value.commonParameters),
  }
}

async function save() {
  const current = draft.value
  if (!current || saving.value || current.source?.status === 'ARCHIVED') return
  let input: EnvironmentCreateInput
  try { input = buildInput() } catch (reason) { error.value = getEnvironmentErrorMessage(reason); return }
  saving.value = true
  error.value = ''
  success.value = ''
  try {
    let saved: Environment
    if (current.source) {
      saved = await updateEnvironment(current.source.id, {
        name: input.name, status: 'ACTIVE', state_version: current.source.state_version,
        environment_variables: input.environment_variables, common_parameters: input.common_parameters,
      })
    } else {
      const created = await createEnvironment(input)
      saved = await updateEnvironment(created.id, {
        name: created.name, status: 'ACTIVE', state_version: created.state_version,
        environment_variables: responseWrites(created.environment_variables),
        common_parameters: responseWrites(created.common_parameters),
      })
    }
    const index = environments.value.findIndex((item) => item.id === saved.id)
    environments.value = index < 0 ? [...environments.value, saved]
      : environments.value.map((item) => item.id === saved.id ? saved : item)
    draft.value = fromEnvironment(saved)
    dirty.value = false
    success.value = '环境配置已保存并激活。'
  } catch (reason) {
    error.value = getEnvironmentErrorMessage(reason)
  } finally { saving.value = false }
}

watch(() => props.projectId, () => { draft.value = null; void loadEnvironments() }, { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section class="environment-view" aria-labelledby="environment-title">
    <header class="page-header"><div><h2 id="environment-title">环境配置</h2><p>按项目维护目标域名与鉴权信息，敏感字段由平台加密保存。</p></div><button class="primary" type="button" :disabled="!projectId || projectsLoading" @click="createDraft">新建环境</button></header>
    <div v-if="projectsLoading" class="state"><span class="loader"></span><strong>正在加载项目</strong></div>
    <div v-else-if="projectsError" class="state error-state"><strong>项目数据不可用</strong><p>{{ projectsError }}</p></div>
    <div v-else-if="!projectId" class="state"><strong>请先选择或新建项目</strong><p>环境配置按项目隔离，请使用右上角项目选择器。</p></div>
    <div v-else-if="loading" class="state"><span class="loader"></span><strong>正在读取环境配置</strong></div>
    <template v-else>
      <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p><p v-if="success" class="notice notice-success" role="status">{{ success }}</p>
      <div class="workspace">
        <aside class="environment-list"><div class="panel-title"><strong>项目环境</strong><span>{{ environments.length }}</span></div><button v-for="item in environments" :key="item.id" class="environment-item" :class="{ active: draft?.source?.id === item.id }" type="button" @click="choose(item)"><strong>{{ item.name }}</strong><span :class="item.status.toLowerCase()">{{ item.status === 'ACTIVE' ? '已激活' : item.status === 'DRAFT' ? '草稿' : '已归档' }}</span><small>修订 v{{ item.revision }}</small></button><div v-if="!environments.length" class="empty-small">暂无环境配置</div></aside>
        <form v-if="draft" class="editor" @submit.prevent="save" @input="markDirty" @change="markDirty">
          <header class="editor-toolbar"><div><strong>{{ draft.source ? '编辑环境' : '新建环境' }}</strong><span v-if="draft.source">修订 v{{ draft.source.revision }}</span></div><div><span v-if="dirty" class="dirty">未保存</span><button class="primary" type="submit" :disabled="saving || draft.source?.status === 'ARCHIVED'">{{ saving ? '保存中…' : '保存并激活' }}</button></div></header>
          <fieldset :disabled="draft.source?.status === 'ARCHIVED' || saving"><legend>基础信息</legend><div class="form-grid"><label><span>环境名称</span><input v-model="draft.name" maxlength="255" placeholder="例如：测试环境" required></label><label><span>基础域名</span><input v-model="draft.baseUrl" type="url" placeholder="https://api.example.com" required></label></div></fieldset>
          <fieldset :disabled="draft.source?.status === 'ARCHIVED' || saving"><legend>鉴权来源</legend><div class="mode-row"><label :class="{ active: draft.authMode === 'DIRECT_TOKEN' }"><input v-model="draft.authMode" type="radio" value="DIRECT_TOKEN"><span><strong>直接 Token</strong><small>使用固定 Token 注入请求头</small></span></label><label :class="{ active: draft.authMode === 'LOGIN' }"><input v-model="draft.authMode" type="radio" value="LOGIN"><span><strong>登录获取 Token</strong><small>运行时登录并提取 Token</small></span></label></div><div v-if="draft.authMode === 'DIRECT_TOKEN'" class="form-grid single"><label><span>直接 Token <em v-if="draft.directToken.ref">已配置</em></span><input v-model="draft.directToken.value" type="password" autocomplete="new-password" :placeholder="draft.directToken.ref ? '留空以保留已配置值' : '输入 Token'" :required="!draft.directToken.ref"></label></div><div v-else class="form-grid"><label class="wide"><span>登录地址</span><input v-model="draft.loginUrl" placeholder="/api/login 或 https://api.example.com/login" required></label><label><span>账号 <em v-if="draft.account.ref">已配置</em></span><input v-model="draft.account.value" type="password" autocomplete="new-password" :placeholder="draft.account.ref ? '留空以保留已配置值' : '输入登录账号'" :required="!draft.account.ref"></label><label><span>密码 <em v-if="draft.password.ref">已配置</em></span><input v-model="draft.password.value" type="password" autocomplete="new-password" :placeholder="draft.password.ref ? '留空以保留已配置值' : '输入登录密码'" :required="!draft.password.ref"></label></div></fieldset>
          <fieldset :disabled="draft.source?.status === 'ARCHIVED' || saving"><legend>高级项</legend><div class="form-grid advanced"><label><span>account field</span><input v-model="draft.accountField" placeholder="username" required></label><label><span>password field</span><input v-model="draft.passwordField" placeholder="password" required></label><label><span>Token 路径</span><input v-model="draft.tokenPath" placeholder="data.token" required></label><label><span>请求头</span><input v-model="draft.authHeader" placeholder="Authorization" required></label><label><span>前缀</span><input v-model="draft.authPrefix" placeholder="Bearer"></label></div></fieldset>
          <p v-if="draft.source?.status === 'ARCHIVED'" class="archived-note">已归档环境不可修改。</p>
        </form>
        <div v-else class="editor-empty"><strong>还没有环境配置</strong><p>新建环境后即可在压测配置中选择。</p><button class="primary" type="button" @click="createDraft">新建环境</button></div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.environment-view{display:flex;flex-direction:column;gap:16px}.page-header{display:flex;align-items:center;justify-content:space-between;gap:16px}.page-header h2{margin:0;color:#1c273a;font-size:20px}.page-header p{margin:6px 0 0;color:#8994a7;font-size:12px}.primary{height:40px;padding:0 17px;border:0;border-radius:8px;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc);font-weight:700;cursor:pointer}.primary:disabled{cursor:not-allowed;opacity:.55}.state{min-height:260px;padding:28px;border:1px solid #e5eaf2;border-radius:14px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;text-align:center;background:#fff}.state p{margin:0;color:#8994a7;font-size:12px}.error-state{border-color:#ffd7db}.loader{width:20px;height:20px;border:3px solid #dbe4f5;border-top-color:#4f72ca;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.notice{margin:0;padding:10px 12px;border-radius:8px;font-size:12px}.notice-error{border:1px solid #ffd7db;color:#b84350;background:#fff5f6}.notice-success{border:1px solid #ccebd8;color:#287a49;background:#f2fbf5}.workspace{min-height:570px;display:grid;grid-template-columns:220px minmax(0,1fr);border:1px solid #e3e8f0;border-radius:14px;overflow:hidden;background:#fff}.environment-list{padding:14px;border-right:1px solid #e8edf4;background:#f8fafc}.panel-title{display:flex;justify-content:space-between;color:#344158;font-size:12px}.panel-title span{color:#8994a7;font-size:10px}.environment-item{width:100%;margin-top:8px;padding:11px;border:1px solid transparent;border-radius:9px;display:grid;grid-template-columns:1fr auto;gap:5px;text-align:left;background:transparent;cursor:pointer}.environment-item:hover,.environment-item.active{background:#fff}.environment-item.active{border-color:#cfdbf8;box-shadow:0 3px 10px rgba(54,79,136,.08)}.environment-item strong{overflow:hidden;color:#344158;font-size:12px;text-overflow:ellipsis}.environment-item span{font-size:9px;font-weight:700}.environment-item span.active{color:#278555}.environment-item span.draft{color:#b47a24}.environment-item span.archived{color:#8994a7}.environment-item small{grid-column:1/-1;color:#929daf;font-size:9px}.empty-small,.editor-empty{color:#9aa4b4;text-align:center;font-size:10px}.empty-small{margin-top:20px}.editor{min-width:0;background:#f7f9fc}.editor-toolbar{min-height:70px;padding:13px 18px;border-bottom:1px solid #e8edf4;display:flex;align-items:center;justify-content:space-between;background:#fff}.editor-toolbar>div{display:flex;align-items:center;gap:10px}.editor-toolbar strong{color:#29364a;font-size:15px}.editor-toolbar span{color:#8994a7;font-size:10px}.editor-toolbar .dirty{color:#b47a24}.editor-toolbar .primary{height:35px}fieldset{margin:14px;padding:15px;border:1px solid #e3e8f0;border-radius:11px;background:#fff}legend{padding:0 7px;color:#344158;font-size:13px;font-weight:750}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.form-grid.single{grid-template-columns:minmax(220px,1fr)}.form-grid.advanced{grid-template-columns:repeat(3,minmax(120px,1fr))}.form-grid label{display:grid;gap:6px;color:#5f6c81;font-size:10px}.form-grid label.wide{grid-column:1/-1}.form-grid input{height:38px;padding:0 10px;border:1px solid #dfe5ee;border-radius:7px;color:#29364a;background:#fbfcfe;font:500 11px inherit}.form-grid input:focus{border-color:#6b8de2;outline:0}.form-grid em{margin-left:5px;padding:2px 5px;border-radius:4px;color:#287a49;background:#edf9f1;font-size:9px;font-style:normal}.mode-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:14px}.mode-row>label{padding:12px;border:1px solid #e0e6ef;border-radius:9px;display:flex;gap:9px;cursor:pointer}.mode-row>label.active{border-color:#7392df;background:#f5f8ff;box-shadow:0 0 0 2px #e9efff}.mode-row span{display:grid;gap:3px}.mode-row strong{color:#354158;font-size:11px}.mode-row small{color:#8e99ab;font-size:9px}.archived-note{margin:14px;padding:10px;border-radius:8px;color:#7d899d;background:#eef1f5;font-size:11px}.editor-empty{min-height:300px;display:flex;flex-direction:column;align-items:center;justify-content:center}.editor-empty strong{color:#637086;font-size:13px}.editor-empty p{margin:5px 0 12px}
@media(max-width:900px){.workspace{grid-template-columns:185px minmax(0,1fr)}.form-grid.advanced{grid-template-columns:repeat(2,minmax(120px,1fr))}}@media(max-width:700px){.workspace{display:block}.environment-list{max-height:220px;overflow:auto;border-right:0;border-bottom:1px solid #e8edf4}.form-grid,.form-grid.advanced,.mode-row{grid-template-columns:1fr}.editor-toolbar{align-items:flex-start;gap:10px;flex-direction:column}.editor-toolbar>div:last-child{width:100%;justify-content:space-between}}@media(max-width:480px){.page-header{align-items:flex-start;flex-direction:column}.page-header>.primary{width:100%}}
</style>