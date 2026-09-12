<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  createWorkflow, debugWorkflow, deleteWorkflow, executeWorkflow, fetchWorkflowRuns,
  fetchWorkflows, getDataFactoryErrorMessage, updateWorkflow,
} from '../services/data-factory'
import type {
  DataFactoryWorkflow, JsonObject, JsonValue, WorkflowEdge, WorkflowEdgeOutcome,
  WorkflowNode, WorkflowNodeType, WorkflowRun,
} from '../types/data-factory'

const props = defineProps<{ projectId: string; projectsLoading: boolean; projectsError: string }>()
const nodeLabels: Record<WorkflowNodeType, string> = {
  http: 'HTTP 请求', condition: '条件判断', parallel: '并行分支',
}
const outcomeLabels: Record<WorkflowEdgeOutcome, string> = {
  always: '始终', success: '成功', failure: '失败', true: '条件为真', false: '条件为假',
}
const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']
const operators = [
  ['equals', '等于'], ['not_equals', '不等于'], ['contains', '包含'], ['exists', '存在'],
  ['gt', '大于'], ['gte', '大于等于'], ['lt', '小于'], ['lte', '小于等于'],
]
const CANVAS_WIDTH = 1000
const CANVAS_HEIGHT = 620
const NODE_WIDTH = 168

const workflows = ref<DataFactoryWorkflow[]>([])
const draft = ref<DataFactoryWorkflow | null>(null)
const selectedNodeId = ref('')
const selectedEdgeId = ref('')
const connectingSource = ref('')
const variablesText = ref('{}')
const jsonEditors = ref<Record<string, string>>({})
const runs = ref<WorkflowRun[]>([])
const selectedRun = ref<WorkflowRun | null>(null)
const loading = ref(false)
const runsLoading = ref(false)
const saving = ref(false)
const executing = ref(false)
const debugging = ref(false)
const deleting = ref(false)
const dirty = ref(false)
const error = ref('')
const success = ref('')
const showCreate = ref(false)
const createName = ref('')
const createDescription = ref('')
const creating = ref(false)
const createError = ref('')
const surface = ref<HTMLElement | null>(null)
let listController: AbortController | null = null
let runsController: AbortController | null = null
let drag: { id: string; offsetX: number; offsetY: number } | null = null

const selectedNode = computed(() => draft.value?.nodes.find((node) => node.id === selectedNodeId.value) ?? null)
const selectedEdge = computed(() => draft.value?.edges.find((edge) => edge.id === selectedEdgeId.value) ?? null)

function cloneWorkflow(workflow: DataFactoryWorkflow): DataFactoryWorkflow {
  return structuredClone(workflow)
}

function setFeedback(message = '') {
  error.value = message
  success.value = ''
}

function markDirty() {
  dirty.value = true
  success.value = ''
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

function jsonText(value: JsonValue | undefined, fallback: JsonValue): string {
  return JSON.stringify(value === undefined ? fallback : value, null, 2)
}

function loadNodeEditors(node: WorkflowNode | null) {
  if (!node) {
    jsonEditors.value = {}
  } else if (node.type === 'http') {
    jsonEditors.value = {
      headers: jsonText(node.config.headers, {}), query: jsonText(node.config.query, {}),
      body: jsonText(node.config.body, null), extractions: jsonText(node.config.extractions, {}),
      expected_statuses: jsonText(node.config.expected_statuses, [200]),
    }
  } else if (node.type === 'condition') {
    jsonEditors.value = { right: jsonText(node.config.right, null) }
  } else jsonEditors.value = {}
}

function parseJson(text: string, label: string): JsonValue {
  try {
    const value: unknown = JSON.parse(text)
    if (!isJsonValue(value)) throw new Error()
    return value
  } catch {
    throw new Error(`${label}必须是有效的 JSON。`)
  }
}

function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isJsonValue)
  return typeof value === 'object' && Object.values(value as Record<string, unknown>).every(isJsonValue)
}

function parseObject(text: string, label: string): JsonObject {
  const value = parseJson(text, label)
  if (value === null || Array.isArray(value) || typeof value !== 'object') {
    throw new Error(`${label}必须是 JSON 对象。`)
  }
  return value
}

function commitNodeEditors(): boolean {
  const node = selectedNode.value
  if (!node) return true
  try {
    if (node.type === 'http') {
      node.config.headers = parseObject(jsonEditors.value.headers ?? '{}', '请求头')
      node.config.query = parseObject(jsonEditors.value.query ?? '{}', '查询参数')
      node.config.body = parseJson(jsonEditors.value.body ?? 'null', '请求体')
      node.config.extractions = parseObject(jsonEditors.value.extractions ?? '{}', '提取规则')
      const statuses = parseJson(jsonEditors.value.expected_statuses ?? '[200]', '期望状态码')
      if (!Array.isArray(statuses) || statuses.some((item) => !Number.isInteger(item))) {
        throw new Error('期望状态码必须是整数数组。')
      }
      node.config.expected_statuses = statuses
    } else if (node.type === 'condition') {
      node.config.right = parseJson(jsonEditors.value.right ?? 'null', '比较值')
    }
    return true
  } catch (reason) {
    setFeedback(getDataFactoryErrorMessage(reason))
    return false
  }
}

function useWorkflow(workflow: DataFactoryWorkflow) {
  draft.value = cloneWorkflow(workflow)
  selectedNodeId.value = ''
  selectedEdgeId.value = ''
  connectingSource.value = ''
  variablesText.value = JSON.stringify(workflow.variables, null, 2)
  runs.value = []
  selectedRun.value = null
  dirty.value = false
  setFeedback()
  void loadRuns(workflow.id)
}

async function loadWorkflows() {
  if (!props.projectId) {
    workflows.value = []
    draft.value = null
    return
  }
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  loading.value = true
  setFeedback()
  try {
    workflows.value = await fetchWorkflows(props.projectId, controller.signal)
    const currentId = draft.value?.id
    const next = workflows.value.find((item) => item.id === currentId) ?? workflows.value[0]
    if (next) useWorkflow(next)
    else draft.value = null
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    setFeedback(getDataFactoryErrorMessage(reason))
  } finally {
    if (listController === controller) loading.value = false
  }
}

async function loadRuns(workflowId: string) {
  runsController?.abort()
  const controller = new AbortController()
  runsController = controller
  runsLoading.value = true
  try {
    runs.value = await fetchWorkflowRuns(workflowId, controller.signal)
    if (!selectedRun.value && runs.value[0]) selectedRun.value = runs.value[0]
  } catch (reason) {
    if (reason instanceof Error && reason.name === 'AbortError') return
    setFeedback(getDataFactoryErrorMessage(reason))
  } finally {
    if (runsController === controller) runsLoading.value = false
  }
}

function chooseWorkflow(workflow: DataFactoryWorkflow) {
  if (draft.value?.id === workflow.id) return
  if (dirty.value && !window.confirm('当前工作流有未保存修改，确定切换并放弃修改吗？')) return
  useWorkflow(workflow)
}

function openCreateDialog() {
  if (dirty.value && !window.confirm('当前工作流有未保存修改。继续新建将放弃这些修改，确定继续吗？')) return
  createName.value = ''
  createDescription.value = ''
  createError.value = ''
  showCreate.value = true
}

async function submitCreate() {
  const name = createName.value.trim()
  if (!name) {
    createError.value = '请填写工作流名称。'
    return
  }
  creating.value = true
  createError.value = ''
  try {
    const created = await createWorkflow({ project_id: props.projectId, name,
      description: createDescription.value.trim(), nodes: [], edges: [], variables: {} })
    workflows.value = [...workflows.value, created]
    showCreate.value = false
    useWorkflow(created)
  } catch (reason) {
    createError.value = getDataFactoryErrorMessage(reason)
  } finally {
    creating.value = false
  }
}

async function removeWorkflow() {
  if (!draft.value || deleting.value || !window.confirm(`确定删除工作流“${draft.value.name}”吗？此操作不可撤销。`)) return
  const id = draft.value.id
  deleting.value = true
  setFeedback()
  try {
    await deleteWorkflow(id)
    workflows.value = workflows.value.filter((item) => item.id !== id)
    const next = workflows.value[0]
    if (next) useWorkflow(next)
    else draft.value = null
  } catch (reason) {
    setFeedback(getDataFactoryErrorMessage(reason))
  } finally {
    deleting.value = false
  }
}

function parsedVariables(): JsonObject {
  return parseObject(variablesText.value, '初始变量')
}

async function persistWorkflow(showMessage = true): Promise<boolean> {
  if (!draft.value || saving.value || !commitNodeEditors()) return false
  const name = draft.value.name.trim()
  if (!name) {
    setFeedback('工作流名称不能为空。')
    return false
  }
  let variables: JsonObject
  try {
    variables = parsedVariables()
  } catch (reason) {
    setFeedback(getDataFactoryErrorMessage(reason))
    return false
  }
  saving.value = true
  setFeedback()
  try {
    const saved = await updateWorkflow(draft.value.id, { project_id: draft.value.project_id, name,
      description: draft.value.description.trim(), nodes: draft.value.nodes,
      edges: draft.value.edges, variables, state_version: draft.value.state_version })
    workflows.value = workflows.value.map((item) => item.id === saved.id ? saved : item)
    draft.value = cloneWorkflow(saved)
    variablesText.value = JSON.stringify(saved.variables, null, 2)
    dirty.value = false
    if (showMessage) success.value = '工作流已保存。'
    return true
  } catch (reason) {
    setFeedback(getDataFactoryErrorMessage(reason))
    return false
  } finally {
    saving.value = false
  }
}

function defaultConfig(type: WorkflowNodeType): JsonObject {
  if (type === 'http') return {
    method: 'GET', url: '', headers: {}, query: {}, body: null, extractions: {},
    timeout_seconds: 30, expected_statuses: [200],
  }
  if (type === 'condition') return { left: '', operator: 'equals', right: null }
  return { max_concurrency: 2 }
}

function addNode(type: WorkflowNodeType) {
  if (!draft.value) return
  if (!commitNodeEditors()) return
  const index = draft.value.nodes.length
  const node: WorkflowNode = { id: crypto.randomUUID(), name: `${nodeLabels[type]} ${index + 1}`, type,
    x: 48 + (index % 4) * 210, y: 54 + Math.floor(index / 4) * 130, config: defaultConfig(type) }
  draft.value.nodes.push(node)
  selectedNodeId.value = node.id
  selectedEdgeId.value = ''
  loadNodeEditors(node)
  markDirty()
}

function chooseNode(node: WorkflowNode) {
  if (connectingSource.value) {
    if (connectingSource.value !== node.id && draft.value) {
      const duplicate = draft.value.edges.some((edge) => edge.source === connectingSource.value && edge.target === node.id)
      if (!duplicate) draft.value.edges.push({ id: crypto.randomUUID(), source: connectingSource.value,
        target: node.id, outcome: 'always' })
      connectingSource.value = ''
      markDirty()
    }
    return
  }
  if (selectedNodeId.value === node.id) return
  if (selectedNodeId.value !== node.id && !commitNodeEditors()) return
  selectedNodeId.value = node.id
  selectedEdgeId.value = ''
  loadNodeEditors(node)
}

function clearSelection() {
  if (!commitNodeEditors()) return
  selectedNodeId.value = ''
  selectedEdgeId.value = ''
  connectingSource.value = ''
}

function chooseEdge(edge: WorkflowEdge) {
  if (!commitNodeEditors()) return
  selectedNodeId.value = ''
  selectedEdgeId.value = edge.id
  connectingSource.value = ''
}

function startConnecting() {
  if (!selectedNode.value) return
  connectingSource.value = selectedNode.value.id
  success.value = '请选择目标节点完成连线。'
}

function removeNode() {
  if (!draft.value || !selectedNode.value) return
  const id = selectedNode.value.id
  draft.value.nodes = draft.value.nodes.filter((node) => node.id !== id)
  draft.value.edges = draft.value.edges.filter((edge) => edge.source !== id && edge.target !== id)
  selectedNodeId.value = ''
  loadNodeEditors(null)
  markDirty()
}

function removeEdge() {
  if (!draft.value || !selectedEdge.value) return
  draft.value.edges = draft.value.edges.filter((edge) => edge.id !== selectedEdge.value?.id)
  selectedEdgeId.value = ''
  markDirty()
}

function configString(key: string, fallback = ''): string {
  const value = selectedNode.value?.config[key]
  return typeof value === 'string' ? value : fallback
}

function configNumber(key: string, fallback: number): number {
  const value = selectedNode.value?.config[key]
  return typeof value === 'number' ? value : fallback
}

function setConfigString(key: string, event: Event) {
  if (!selectedNode.value) return
  selectedNode.value.config[key] = (event.target as HTMLInputElement | HTMLSelectElement).value
  markDirty()
}

function setConfigNumber(key: string, event: Event) {
  if (!selectedNode.value) return
  const value = Number((event.target as HTMLInputElement).value)
  selectedNode.value.config[key] = Number.isFinite(value) ? value : 1
  markDirty()
}

function edgeOutcomes(edge: WorkflowEdge): WorkflowEdgeOutcome[] {
  const sourceType = draft.value?.nodes.find((node) => node.id === edge.source)?.type
  if (sourceType === 'http') return ['always', 'success', 'failure']
  if (sourceType === 'condition') return ['always', 'true', 'false']
  return ['always']
}

function editJson(key: string, event: Event) {
  jsonEditors.value[key] = (event.target as HTMLTextAreaElement).value
  markDirty()
}

function edgePath(edge: WorkflowEdge): string {
  const sourceNode = draft.value?.nodes.find((node) => node.id === edge.source)
  const targetNode = draft.value?.nodes.find((node) => node.id === edge.target)
  if (!sourceNode || !targetNode) return ''
  const x1 = sourceNode.x + NODE_WIDTH
  const y1 = sourceNode.y + 35
  const x2 = targetNode.x
  const y2 = targetNode.y + 35
  const curve = Math.max(55, Math.abs(x2 - x1) * .45)
  return `M ${x1} ${y1} C ${x1 + curve} ${y1}, ${x2 - curve} ${y2}, ${x2} ${y2}`
}

function startDrag(event: PointerEvent, node: WorkflowNode) {
  if (event.button !== 0 || connectingSource.value) return
  chooseNode(node)
  if (selectedNodeId.value !== node.id) return
  const rect = surface.value?.getBoundingClientRect()
  if (!rect) return
  drag = { id: node.id, offsetX: event.clientX - rect.left - node.x,
    offsetY: event.clientY - rect.top - node.y }
  event.preventDefault()
}

function moveDrag(event: PointerEvent) {
  if (!drag || !draft.value || !surface.value) return
  const node = draft.value.nodes.find((item) => item.id === drag?.id)
  if (!node) return
  const rect = surface.value.getBoundingClientRect()
  node.x = Math.round(Math.max(8, Math.min(CANVAS_WIDTH - NODE_WIDTH - 8, event.clientX - rect.left - drag.offsetX)))
  node.y = Math.round(Math.max(8, Math.min(CANVAS_HEIGHT - 78, event.clientY - rect.top - drag.offsetY)))
  markDirty()
}

function stopDrag() {
  drag = null
}

async function runWorkflow() {
  if (!draft.value || executing.value) return
  const workflowId = draft.value.id
  if (dirty.value && !await persistWorkflow(false)) return
  let variables: JsonObject
  try { variables = parsedVariables() } catch (reason) {
    setFeedback(getDataFactoryErrorMessage(reason)); return
  }
  executing.value = true
  setFeedback()
  try {
    const run = await executeWorkflow(workflowId, { variables })
    selectedRun.value = run
    runs.value = [run, ...runs.value.filter((item) => item.id !== run.id)]
    success.value = '工作流执行已提交。'
    void loadRuns(workflowId)
  } catch (reason) { setFeedback(getDataFactoryErrorMessage(reason)) }
  finally { executing.value = false }
}

async function debugNode() {
  const nodeId = selectedNode.value?.id
  if (!draft.value || !nodeId || debugging.value) return
  const workflowId = draft.value.id
  if (dirty.value && !await persistWorkflow(false)) return
  let variables: JsonObject
  try { variables = parsedVariables() } catch (reason) {
    setFeedback(getDataFactoryErrorMessage(reason)); return
  }
  debugging.value = true
  setFeedback()
  try {
    const run = await debugWorkflow(workflowId, { node_id: nodeId, variables })
    selectedRun.value = run
    runs.value = [run, ...runs.value.filter((item) => item.id !== run.id)]
    success.value = '节点调试已提交。'
  } catch (reason) { setFeedback(getDataFactoryErrorMessage(reason)) }
  finally { debugging.value = false }
}

watch(() => props.projectId, () => {
  runsController?.abort()
  draft.value = null
  void loadWorkflows()
}, { immediate: true })

onMounted(() => {
  window.addEventListener('pointermove', moveDrag)
  window.addEventListener('pointerup', stopDrag)
})
onBeforeUnmount(() => {
  listController?.abort()
  runsController?.abort()
  window.removeEventListener('pointermove', moveDrag)
  window.removeEventListener('pointerup', stopDrag)
})
</script>

<template>
  <section class="factory" aria-labelledby="factory-title">
    <header class="factory-header">
      <div><h2 id="factory-title">数据工厂</h2><p>以可视化工作流编排请求、条件和并行任务，快速准备测试数据。</p></div>
      <button class="primary" type="button" :disabled="!projectId || projectsLoading" @click="openCreateDialog">新建工作流</button>
    </header>

    <div v-if="projectsLoading" class="state"><span class="loader"></span><strong>正在加载项目</strong></div>
    <div v-else-if="projectsError" class="state error-state"><strong>项目数据不可用</strong><p>{{ projectsError }}</p></div>
    <div v-else-if="!projectId" class="state"><strong>请先选择或新建项目</strong><p>数据工厂工作流按项目隔离，请使用右上角项目选择器。</p></div>
    <div v-else-if="loading" class="state"><span class="loader"></span><strong>正在读取工作流</strong></div>
    <div v-else-if="error && workflows.length === 0" class="state error-state"><strong>工作流暂时不可用</strong><p>{{ error }}</p><button type="button" @click="loadWorkflows">重新连接</button></div>
    <template v-else>
      <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>
      <p v-if="success" class="notice notice-success" role="status">{{ success }}</p>
      <div class="factory-workspace">
        <aside class="workflow-list">
          <div class="panel-title"><strong>工作流</strong><span>{{ workflows.length }}</span></div>
          <button v-for="workflow in workflows" :key="workflow.id" type="button" class="workflow-item" :class="{ active: draft?.id === workflow.id }" @click="chooseWorkflow(workflow)">
            <strong>{{ workflow.name }}</strong><small>{{ workflow.nodes.length }} 个节点 · {{ formatDate(workflow.updated_at) }}</small>
          </button>
          <div v-if="workflows.length === 0" class="empty-small">暂无工作流<br>点击“新建工作流”开始编排</div>
        </aside>

        <div v-if="draft" class="editor">
          <div class="editor-toolbar">
            <div class="workflow-fields">
              <input v-model="draft.name" maxlength="255" aria-label="工作流名称" @input="markDirty">
              <input v-model="draft.description" maxlength="100000" placeholder="添加工作流描述" aria-label="工作流描述" @input="markDirty">
            </div>
            <div class="toolbar-actions">
              <span v-if="dirty" class="dirty-mark">未保存</span>
              <button class="secondary danger" type="button" :disabled="deleting" @click="removeWorkflow">{{ deleting ? '删除中…' : '删除' }}</button>
              <button class="secondary" type="button" :disabled="saving" @click="persistWorkflow()">{{ saving ? '保存中…' : '保存' }}</button>
              <button class="primary" type="button" :disabled="executing || saving" @click="runWorkflow">{{ executing ? '执行中…' : '执行工作流' }}</button>
            </div>
          </div>

          <div class="editor-grid">
            <aside class="node-palette">
              <div class="panel-title"><strong>添加节点</strong></div>
              <button type="button" @click="addNode('http')"><i class="type-http">H</i><span><strong>HTTP 请求</strong><small>调用接口并提取数据</small></span></button>
              <button type="button" @click="addNode('condition')"><i class="type-condition">?</i><span><strong>条件判断</strong><small>按变量选择分支</small></span></button>
              <button type="button" @click="addNode('parallel')"><i class="type-parallel">Ⅱ</i><span><strong>并行分支</strong><small>并发执行后续节点</small></span></button>
              <p class="palette-tip">拖动节点调整位置；选择节点后点击“创建连线”，再选择目标节点。</p>
            </aside>

            <div class="canvas-scroll">
              <div ref="surface" class="canvas-surface" :style="{ width: `${CANVAS_WIDTH}px`, height: `${CANVAS_HEIGHT}px` }" @pointerdown.self="clearSelection">
                <svg class="edge-layer" :viewBox="`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`">
                  <defs><marker id="factory-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" /></marker></defs>
                  <g v-for="edge in draft.edges" :key="edge.id" @click.stop="chooseEdge(edge)">
                    <path class="edge-hit" :d="edgePath(edge)" />
                    <path class="edge-line" :class="{ selected: selectedEdgeId === edge.id }" :d="edgePath(edge)" marker-end="url(#factory-arrow)" />
                    <text :x="(draft.nodes.find(n => n.id === edge.source)?.x ?? 0) + NODE_WIDTH + 8" :y="(draft.nodes.find(n => n.id === edge.source)?.y ?? 0) + 27">{{ outcomeLabels[edge.outcome] }}</text>
                  </g>
                </svg>
                <div v-for="node in draft.nodes" :key="node.id" class="canvas-node" :class="[`node-${node.type}`, { selected: selectedNodeId === node.id, connecting: connectingSource === node.id }]" :style="{ left: `${node.x}px`, top: `${node.y}px`, width: `${NODE_WIDTH}px` }" role="button" tabindex="0" @pointerdown.stop="startDrag($event, node)" @click.stop="chooseNode(node)" @keydown.enter="chooseNode(node)">
                  <span class="node-icon">{{ node.type === 'http' ? 'H' : node.type === 'condition' ? '?' : 'Ⅱ' }}</span>
                  <span><strong>{{ node.name }}</strong><small>{{ nodeLabels[node.type] }}</small></span><i class="port"></i>
                </div>
                <div v-if="draft.nodes.length === 0" class="canvas-empty"><strong>画布还是空的</strong><span>从左侧添加第一个节点</span></div>
              </div>
            </div>

            <aside class="inspector">
              <template v-if="selectedNode">
                <div class="panel-title"><strong>节点配置</strong><span>{{ nodeLabels[selectedNode.type] }}</span></div>
                <label><span>节点名称</span><input v-model="selectedNode.name" maxlength="255" @input="markDirty"></label>
                <template v-if="selectedNode.type === 'http'">
                  <label><span>请求方法</span><select :value="configString('method', 'GET')" @change="setConfigString('method', $event)"><option v-for="method in methods" :key="method">{{ method }}</option></select></label>
                  <label><span>请求 URL（支持变量模板）</span><input :value="configString('url')" placeholder="https://api.example.com/users/{{user_id}}" @input="setConfigString('url', $event)"></label>
                  <label><span>超时秒数</span><input type="number" min="1" max="120" :value="configNumber('timeout_seconds', 30)" @input="setConfigNumber('timeout_seconds', $event)"></label>
                  <label><span>期望状态码 JSON 数组</span><textarea :value="jsonEditors.expected_statuses" rows="2" @input="editJson('expected_statuses', $event)"></textarea></label>
                  <label><span>请求头 JSON</span><textarea :value="jsonEditors.headers" rows="4" @input="editJson('headers', $event)"></textarea></label>
                  <label><span>查询参数 JSON</span><textarea :value="jsonEditors.query" rows="4" @input="editJson('query', $event)"></textarea></label>
                  <label><span>请求体 JSON</span><textarea :value="jsonEditors.body" rows="5" @input="editJson('body', $event)"></textarea></label>
                  <label><span>提取规则 JSON</span><textarea :value="jsonEditors.extractions" rows="4" placeholder='{ "token": "$.data.token" }' @input="editJson('extractions', $event)"></textarea></label>
                </template>
                <template v-else-if="selectedNode.type === 'condition'">
                  <label><span>左值 / 变量表达式</span><input :value="configString('left')" placeholder="{{status_code}}" @input="setConfigString('left', $event)"></label>
                  <label><span>运算符</span><select :value="configString('operator', 'equals')" @change="setConfigString('operator', $event)"><option v-for="option in operators" :key="option[0]" :value="option[0]">{{ option[1] }}</option></select></label>
                  <label><span>比较值 JSON</span><textarea :value="jsonEditors.right" rows="4" @input="editJson('right', $event)"></textarea></label>
                </template>
                <template v-else>
                  <label><span>最大并发数</span><input type="number" min="1" max="100" :value="configNumber('max_concurrency', 2)" @input="setConfigNumber('max_concurrency', $event)"></label>
                </template>
                <div class="inspector-actions"><button class="secondary" type="button" :disabled="debugging" @click="debugNode">{{ debugging ? '调试中…' : '单节点调试' }}</button><button class="secondary" type="button" @click="startConnecting">创建连线</button><button class="text-danger" type="button" @click="removeNode">删除节点</button></div>
              </template>
              <template v-else-if="selectedEdge">
                <div class="panel-title"><strong>连线配置</strong></div>
                <p class="edge-summary">{{ draft.nodes.find(n => n.id === selectedEdge?.source)?.name }} → {{ draft.nodes.find(n => n.id === selectedEdge?.target)?.name }}</p>
                <label><span>触发结果</span><select v-model="selectedEdge.outcome" @change="markDirty"><option v-for="outcome in edgeOutcomes(selectedEdge)" :key="outcome" :value="outcome">{{ outcomeLabels[outcome] }}</option></select></label>
                <button class="text-danger" type="button" @click="removeEdge">删除连线</button>
              </template>
              <div v-else class="inspector-empty"><strong>配置面板</strong><p>选择画布中的节点或连线进行配置。</p></div>
              <div class="variables-editor"><div class="panel-title"><strong>初始变量</strong><span>JSON</span></div><textarea v-model="variablesText" rows="8" aria-label="初始变量 JSON" @input="markDirty"></textarea><small>执行与调试时作为默认变量传入，可使用 JSON 对象覆盖。</small></div>
            </aside>
          </div>

          <section class="runs-panel">
            <header><div><h3>最近执行</h3><span v-if="runsLoading">刷新中…</span></div><button type="button" :disabled="runsLoading" @click="loadRuns(draft.id)">刷新</button></header>
            <div class="runs-content">
              <div class="run-list"><button v-for="run in runs.slice(0, 8)" :key="run.id" type="button" :class="{ active: selectedRun?.id === run.id }" @click="selectedRun = run"><span class="run-status" :class="run.status.toLowerCase()">{{ run.status }}</span><small>{{ formatDate(run.started_at) }}</small></button><p v-if="!runsLoading && runs.length === 0">暂无执行记录</p></div>
              <div v-if="selectedRun" class="run-result"><div><strong>执行结果 · {{ selectedRun.status }}</strong><span>开始 {{ formatDate(selectedRun.started_at) }} · 完成 {{ formatDate(selectedRun.finished_at) }}</span></div><pre>{{ JSON.stringify({ node_results: selectedRun.node_results, variables: selectedRun.variables }, null, 2) }}</pre></div>
              <div v-else class="run-result-empty">选择执行记录查看节点结果</div>
            </div>
          </section>
        </div>
      </div>
    </template>

    <div v-if="showCreate" class="modal" role="dialog" aria-modal="true" aria-label="新建数据工厂工作流">
      <form class="modal-card" @submit.prevent="submitCreate"><header><h3>新建工作流</h3><button type="button" :disabled="creating" @click="showCreate = false">×</button></header><div class="modal-body"><label><span>名称</span><input v-model="createName" maxlength="255" autofocus placeholder="例如：订单测试数据准备"></label><label><span>描述（可选）</span><textarea v-model="createDescription" rows="3" maxlength="100000" placeholder="说明此工作流准备的数据和用途"></textarea></label><p v-if="createError" class="notice notice-error">{{ createError }}</p></div><footer><button class="secondary" type="button" :disabled="creating" @click="showCreate = false">取消</button><button class="primary" type="submit" :disabled="creating">{{ creating ? '创建中…' : '创建工作流' }}</button></footer></form>
    </div>
  </section>
</template>

<style scoped>
.factory{display:flex;flex-direction:column;gap:16px}.factory-header{display:flex;align-items:center;justify-content:space-between;gap:16px}.factory-header h2{margin:0;color:#1c273a;font-size:20px}.factory-header p{margin:6px 0 0;color:#8994a7;font-size:12px}.primary,.secondary{border-radius:8px;font-weight:700;cursor:pointer}.primary{height:40px;padding:0 17px;border:0;color:#fff;background:linear-gradient(100deg,#456fd6,#6b60dc)}.secondary{height:34px;padding:0 12px;border:1px solid #dfe5ee;color:#556176;background:#fff}.primary:disabled,.secondary:disabled{cursor:not-allowed;opacity:.55}.secondary.danger,.text-danger{color:#bd4654}.state{min-height:260px;padding:28px;border:1px solid #e5eaf2;border-radius:14px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;text-align:center;background:#fff}.state p{max-width:440px;margin:0;color:#8994a7;font-size:12px}.state button{margin-top:8px}.error-state{border-color:#ffd7db}.loader{width:20px;height:20px;border:3px solid #dbe4f5;border-top-color:#4f72ca;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.notice{margin:0;padding:10px 12px;border-radius:8px;font-size:12px}.notice-error{border:1px solid #ffd7db;color:#b84350;background:#fff5f6}.notice-success{border:1px solid #ccebd8;color:#287a49;background:#f2fbf5}.factory-workspace{min-height:640px;display:grid;grid-template-columns:210px minmax(0,1fr);border:1px solid #e3e8f0;border-radius:14px;overflow:hidden;background:#fff}.workflow-list{padding:14px;border-right:1px solid #e8edf4;background:#f8fafc}.panel-title{min-height:26px;display:flex;align-items:center;justify-content:space-between;gap:8px;color:#344158;font-size:12px}.panel-title span{color:#8994a7;font-size:10px}.workflow-item{width:100%;display:grid;gap:4px;margin-top:7px;padding:11px;border:1px solid transparent;border-radius:9px;text-align:left;background:transparent;cursor:pointer}.workflow-item:hover{background:#fff}.workflow-item.active{border-color:#cfdbf8;background:#fff;box-shadow:0 3px 10px rgba(54,79,136,.08)}.workflow-item strong{overflow:hidden;color:#344158;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.workflow-item small{color:#929daf;font-size:9px}.empty-small{margin-top:18px;color:#9aa4b4;text-align:center;font-size:10px;line-height:1.7}.editor{min-width:0}.editor-toolbar{min-height:66px;padding:10px 14px;border-bottom:1px solid #e8edf4;display:flex;align-items:center;justify-content:space-between;gap:12px}.workflow-fields{min-width:160px;display:grid;gap:4px}.workflow-fields input{padding:2px 5px;border:1px solid transparent;border-radius:5px;background:transparent}.workflow-fields input:first-child{color:#243149;font-size:14px;font-weight:750}.workflow-fields input:last-child{color:#8792a5;font-size:10px}.workflow-fields input:focus{border-color:#b8c9ef;background:#fff;outline:0}.toolbar-actions{display:flex;align-items:center;gap:7px}.dirty-mark{color:#b47a24;font-size:10px;white-space:nowrap}.editor-grid{display:grid;grid-template-columns:132px minmax(420px,1fr) 270px;min-height:620px}.node-palette{padding:12px;border-right:1px solid #e8edf4;background:#fbfcfe}.node-palette>button{width:100%;display:flex;align-items:center;gap:8px;margin:8px 0;padding:9px 7px;border:1px solid #e2e7ef;border-radius:9px;text-align:left;background:#fff;cursor:pointer}.node-palette i{width:25px;height:25px;display:grid;place-items:center;border-radius:7px;color:#fff;font-size:11px;font-style:normal;font-weight:800}.node-palette button span{min-width:0;display:grid;gap:2px}.node-palette button strong{color:#344158;font-size:10px}.node-palette button small{color:#98a2b3;font-size:8px}.type-http{background:#4e78da}.type-condition{background:#d39436}.type-parallel{background:#795ed0}.palette-tip{margin:16px 2px;color:#919bad;font-size:9px;line-height:1.6}.canvas-scroll{position:relative;overflow:auto;background:#f5f7fb}.canvas-surface{position:relative;background-color:#f7f9fc;background-image:radial-gradient(#cbd4e3 .7px,transparent .7px);background-size:18px 18px;user-select:none}.edge-layer{position:absolute;inset:0;width:100%;height:100%;overflow:visible}.edge-line{fill:none;stroke:#99a8c0;stroke-width:2;pointer-events:none}.edge-line.selected{stroke:#4f73d2;stroke-width:3}.edge-hit{fill:none;stroke:transparent;stroke-width:14;cursor:pointer}.edge-layer marker path{fill:#99a8c0}.edge-layer text{fill:#77859a;font-size:9px;pointer-events:none}.canvas-node{position:absolute;height:70px;padding:10px 16px 10px 10px;border:2px solid #dce3ed;border-left-width:4px;border-radius:10px;display:flex;align-items:center;gap:9px;background:#fff;box-shadow:0 5px 15px rgba(33,48,80,.08);cursor:grab;touch-action:none}.canvas-node.selected{border-color:#6c8dde;box-shadow:0 0 0 3px #e2eaff}.canvas-node.connecting{border-color:#6b60dc;animation:pulse 1.2s infinite}@keyframes pulse{50%{box-shadow:0 0 0 6px rgba(107,96,220,.13)}}.canvas-node:active{cursor:grabbing}.node-http{border-left-color:#4e78da}.node-condition{border-left-color:#d39436}.node-parallel{border-left-color:#795ed0}.node-icon{width:27px;height:27px;flex:0 0 auto;display:grid;place-items:center;border-radius:7px;color:#fff;background:#5b7bd0;font-size:11px;font-weight:800}.node-condition .node-icon{background:#d39436}.node-parallel .node-icon{background:#795ed0}.canvas-node>span:nth-child(2){min-width:0;display:grid;gap:3px}.canvas-node strong{overflow:hidden;color:#2e3a50;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.canvas-node small{color:#909bad;font-size:9px}.port{position:absolute;right:-7px;width:12px;height:12px;border:2px solid #fff;border-radius:50%;background:#8b9ab2}.canvas-empty{position:absolute;left:50%;top:44%;display:grid;gap:5px;text-align:center;transform:translate(-50%,-50%);color:#9aa5b7}.canvas-empty strong{font-size:13px}.canvas-empty span{font-size:10px}.inspector{min-width:0;padding:12px;border-left:1px solid #e8edf4;overflow-y:auto;background:#fff}.inspector label,.modal label{display:grid;gap:5px;margin:9px 0;color:#5f6c81;font-size:10px}.inspector input,.inspector select,.inspector textarea,.modal input,.modal textarea{width:100%;border:1px solid #dfe5ee;border-radius:7px;color:#29364a;background:#fbfcfe;font:500 11px inherit}.inspector input,.inspector select,.modal input{height:34px;padding:0 9px}.inspector textarea,.modal textarea{padding:8px;resize:vertical;font-family:Consolas,"Microsoft YaHei",monospace;line-height:1.45}.inspector input:focus,.inspector select:focus,.inspector textarea:focus{border-color:#6b8de2;outline:0}.inspector-actions{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}.text-danger{padding:0;border:0;background:transparent;font-size:10px;font-weight:700;cursor:pointer}.edge-summary{padding:9px;border-radius:7px;color:#5e6b80;background:#f5f7fb;font-size:10px;line-height:1.5}.inspector-empty{padding:22px 8px;text-align:center;color:#8f9aac}.inspector-empty strong{font-size:12px}.inspector-empty p{font-size:10px;line-height:1.6}.variables-editor{margin-top:14px;padding-top:12px;border-top:1px solid #e8edf4}.variables-editor textarea{width:100%;border:1px solid #dfe5ee;border-radius:7px;padding:8px;resize:vertical;color:#29364a;background:#fbfcfe;font:10px/1.5 Consolas,monospace}.variables-editor small{display:block;margin-top:5px;color:#929daf;font-size:9px;line-height:1.5}.runs-panel{border-top:1px solid #e8edf4}.runs-panel>header{padding:11px 14px;display:flex;align-items:center;justify-content:space-between}.runs-panel h3{display:inline;margin:0;color:#344158;font-size:13px}.runs-panel header span{margin-left:8px;color:#8f9aac;font-size:9px}.runs-panel header button{border:0;color:#5071c7;background:transparent;font-size:10px;font-weight:700;cursor:pointer}.runs-content{min-height:190px;display:grid;grid-template-columns:220px minmax(0,1fr);border-top:1px solid #eef1f6}.run-list{padding:9px;border-right:1px solid #eef1f6;background:#fafbfd}.run-list button{width:100%;padding:8px;border:1px solid transparent;border-radius:7px;display:flex;align-items:center;justify-content:space-between;background:transparent;cursor:pointer}.run-list button.active{border-color:#d8e2f8;background:#fff}.run-list small,.run-list p{color:#909bad;font-size:9px}.run-status{max-width:100px;overflow:hidden;color:#4d70ca;font-size:9px;font-weight:800;text-overflow:ellipsis}.run-status.succeeded,.run-status.success{color:#278555}.run-status.failed,.run-status.failure{color:#bf4654}.run-result{min-width:0;padding:12px}.run-result>div{display:flex;justify-content:space-between;gap:10px;color:#344158;font-size:10px}.run-result>div span{color:#929daf;font-size:9px}.run-result pre{max-height:220px;overflow:auto;margin:10px 0 0;padding:10px;border-radius:7px;color:#4a5870;background:#f5f7fb;font:10px/1.5 Consolas,monospace;white-space:pre-wrap}.run-result-empty{display:grid;place-items:center;color:#9aa5b6;font-size:10px}.modal{position:fixed;inset:0;z-index:60;padding:20px;display:grid;place-items:center;background:rgba(20,28,45,.42)}.modal-card{width:min(500px,100%);border-radius:14px;background:#fff;box-shadow:0 24px 60px rgba(20,28,45,.28);overflow:hidden}.modal-card header,.modal-card footer{padding:16px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #eef1f6}.modal-card h3{margin:0;color:#26334b;font-size:15px}.modal-card header button{width:28px;height:28px;border:0;border-radius:7px;color:#66748a;background:#f0f3f7;cursor:pointer}.modal-body{padding:14px 20px}.modal-card footer{justify-content:flex-end;gap:8px;border-top:1px solid #eef1f6;border-bottom:0}.modal textarea{font-family:inherit}
@media(max-width:1120px){.factory-workspace{grid-template-columns:180px minmax(0,1fr)}.editor-grid{grid-template-columns:115px minmax(420px,1fr)}.inspector{grid-column:1/-1;border-top:1px solid #e8edf4;border-left:0;max-height:none}.inspector label{max-width:620px}.variables-editor{max-width:620px}}
@media(max-width:760px){.factory-header,.editor-toolbar{align-items:flex-start;flex-direction:column}.factory-workspace{display:block}.workflow-list{max-height:210px;overflow-y:auto;border-right:0;border-bottom:1px solid #e8edf4}.toolbar-actions{width:100%;overflow-x:auto}.editor-grid{display:block}.node-palette{display:flex;align-items:center;gap:6px;overflow-x:auto;border-right:0;border-bottom:1px solid #e8edf4}.node-palette .panel-title,.palette-tip{display:none}.node-palette>button{min-width:130px}.canvas-scroll{height:500px}.inspector{border-left:0}.runs-content{grid-template-columns:1fr}.run-list{max-height:150px;overflow:auto;border-right:0;border-bottom:1px solid #eef1f6}.run-result>div{flex-direction:column}}
</style>