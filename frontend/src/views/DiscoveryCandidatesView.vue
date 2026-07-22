<template>
  <div class="page-heading">
    <div>
      <h1 class="page-title">品牌候选池</h1>
      <span class="muted">匹配评估用于辅助判断，不是精准丰富的前置条件；用户确认后即可查找联系人和邮箱</span>
    </div>
    <el-button :loading="loading" @click="load()">刷新</el-button>
  </div>

  <div v-if="currentTask" class="panel task-banner" aria-live="polite">
    <div class="task-banner-title">
      当前搜索：<strong>{{ currentTask.task_name }}</strong>
      <el-tag size="small" style="margin-left: 8px">{{ currentTask.task_status || '未知状态' }}</el-tag>
    </div>
    <div class="task-banner-body">
      <span v-if="currentTask.original_prompt">搜索目标：{{ currentTask.original_prompt }}</span>
      <span v-if="currentTask.target_concepts?.length">
        目标概念：
        <el-tag v-for="concept in currentTask.target_concepts" :key="concept.id || concept.source_text" size="small" type="info" style="margin-right: 4px">
          {{ concept.source_text || concept.normalized_label }}
        </el-tag>
      </span>
      <span v-if="taskFilterText" class="muted">{{ taskFilterText }}</span>
    </div>
  </div>

  <div v-else-if="isAllTasksView" class="panel task-banner" aria-live="polite">
    <div class="task-banner-title"><strong>全部数据</strong></div>
    <div class="task-banner-body muted">
      当前展示所有搜索任务中的待处理候选，不包含正在或已经精准丰富的数据；相关度仅供判断。单条候选可直接精准丰富，批量操作请先切换到具体搜索任务。
    </div>
  </div>

  <div class="panel">
    <div class="table-toolbar">
      <el-select v-model="taskFilter" placeholder="选择搜索任务" filterable style="width: 260px" @change="onTaskFilterChange">
        <el-option label="全部待处理数据（不含精准丰富）" :value="ALL_TASKS" />
        <el-option v-for="task in taskOptions" :key="task.id" :label="task.name" :value="task.id" />
      </el-select>
      <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width: 150px" @change="onFilterChange">
        <el-option label="待审核" value="pending" />
        <el-option label="精准丰富中" value="enriching" />
        <el-option label="丰富失败" value="enrichment_failed" />
        <el-option label="已转入客户数据" value="promoted" />
        <el-option label="已拒绝" value="rejected" />
      </el-select>
      <span class="muted">共 {{ total }} 条候选</span>
      <el-button @click="selectPageApprovable">选择本页可丰富候选</el-button>
      <el-button type="primary" :disabled="!selectedIds.length" @click="openBulkApprove">
        批量精准丰富（{{ selectedIds.length }}）
      </el-button>
    </div>

    <el-table
      ref="tableRef"
      v-loading="loading"
      :data="items"
      row-key="id"
      border
      style="width: 100%"
      @selection-change="onSelectionChange"
    >
      <el-table-column type="selection" width="48" reserve-selection />
      <el-table-column prop="name" label="品牌/公司" min-width="160" show-overflow-tooltip />
      <el-table-column prop="domain" label="官网域名" min-width="160">
        <template #default="scope">
          <a v-if="scope.row.website" :href="scope.row.website" target="_blank" rel="noreferrer">
            {{ scope.row.domain || scope.row.website }}
          </a>
          <span v-else>{{ scope.row.domain || '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="country" label="国家" width="90" />
      <el-table-column prop="industry" label="行业/品类" min-width="170" show-overflow-tooltip>
        <template #default="scope">
          <span :class="{ muted: !scope.row.industry }">{{ scope.row.industry || '暂未识别' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="邮箱数据" width="150">
        <template #default="scope">
          <el-tag :type="emailDataStatus(scope.row).type" effect="plain">
            {{ emailDataStatus(scope.row).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="大概相关性" width="180">
        <template #default="scope">
          <el-tag :type="relevanceHintType(scope.row.relevance_hint?.level)" effect="plain">
            {{ scope.row.relevance_hint?.label || '待判断' }}
          </el-tag>
          <div class="muted" style="font-size: 12px; margin-top: 4px">{{ scope.row.relevance_hint?.reason || '建议查看官网' }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="120">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_seen_at" label="最近发现" width="160">
        <template #default="scope">{{ formatDate(scope.row.last_seen_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="270" fixed="right">
        <template #default="scope">
          <el-button
            v-if="['pending', 'enrichment_failed'].includes(scope.row.status)"
            size="small"
            type="primary"
            :loading="actionId === scope.row.id"
            :disabled="!canApprove(scope.row)"
            @click="openApproval(scope.row)"
          >
            {{ canApprove(scope.row) ? '精准丰富' : '缺少官网' }}
          </el-button>
          <el-button
            v-if="scope.row.status !== 'promoted' && scope.row.status !== 'rejected'"
            size="small"
            type="danger"
            plain
            :disabled="actionId === scope.row.id"
            @click="reject(scope.row)"
          >
            拒绝
          </el-button>
          <span v-if="scope.row.status === 'promoted'" class="muted">已进入客户库</span>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="prev, pager, next, total"
      style="margin-top: 16px; justify-content: flex-end"
      @current-change="load"
    />
  </div>

  <el-dialog v-model="approvalVisible" title="批准候选品牌" width="620px">
    <el-alert v-if="approvalCandidate" :title="`将使用 ${approvalCandidate.domain || approvalCandidate.website || approvalCandidate.name} 发起精准品牌联系人和邮箱查找`" type="warning" :closable="false" show-icon style="margin-bottom: 16px" />
    <el-form label-width="120px">
      <el-form-item label="目标职位" required>
        <div style="width: 100%">
          <el-input v-model="approvalTitles" type="textarea" :rows="10" placeholder="每行一个职位，可直接增删或补充" aria-label="目标职位列表" />
          <div class="title-actions">
            <span class="muted">当前共 {{ splitTitles(approvalTitles).length }} 个职位</span>
            <el-button link type="primary" :loading="loadingTitles" @click="loadAllSystemTitles">载入系统全部职位</el-button>
          </div>
        </div>
      </el-form-item>
      <el-form-item label="每品牌联系人"><el-input-number v-model="approvalContactLimit" :min="1" :max="50" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="approvalVisible = false">取消</el-button>
      <el-button type="primary" :loading="!!actionId" @click="submitApproval">开始精准丰富</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="bulkVisible" title="批量精准丰富" width="640px">
    <el-alert
      :title="`将对 ${selectedIds.length} 个候选发起精准品牌联系人和邮箱查找，可能产生 Provider 费用`"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    />
    <p class="muted" style="margin-top: 0">
      服务端会逐条校验候选状态和官网域名；匹配评级仅供参考，不会阻止用户发起精准丰富。
    </p>
    <template v-if="bulkResult">
      <el-result
        icon="info"
        :title="`成功 ${bulkResult.approved} · 跳过 ${bulkResult.skipped} · 失败 ${bulkResult.failed}`"
        style="padding: 12px 0"
      />
      <el-table v-if="bulkResult.items.length" :data="bulkResult.items" size="small" border max-height="260">
        <el-table-column label="候选" min-width="140">
          <template #default="scope">{{ nameOf(scope.row.candidate_id) }}</template>
        </el-table-column>
        <el-table-column label="结果" width="90">
          <template #default="scope">
            <el-tag size="small" :type="scope.row.status === 'approved' ? 'success' : scope.row.status === 'skipped' ? 'info' : 'danger'">
              {{ bulkStatusLabel(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="说明" min-width="200" show-overflow-tooltip />
      </el-table>
    </template>
    <el-form v-else label-width="120px">
      <el-form-item label="目标职位" required>
        <div style="width: 100%">
          <el-input v-model="approvalTitles" type="textarea" :rows="8" placeholder="每行一个职位" aria-label="批量精准丰富目标职位列表" />
          <div class="title-actions">
            <span class="muted">当前共 {{ splitTitles(approvalTitles).length }} 个职位</span>
            <el-button link type="primary" :loading="loadingTitles" @click="loadAllSystemTitles">载入系统全部职位</el-button>
          </div>
        </div>
      </el-form-item>
      <el-form-item label="每品牌联系人"><el-input-number v-model="approvalContactLimit" :min="1" :max="50" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="closeBulk">{{ bulkResult ? '关闭' : '取消' }}</el-button>
      <el-button v-if="!bulkResult" type="primary" :loading="bulkApproving" @click="submitBulkApprove">
        确认发起（可能产生费用）
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import { safeCandidateStatus } from '../api/compat'

interface EnrichmentFailure {
  code: string
  message: string
  reset_at?: string | null
}

interface Candidate {
  id: string
  name: string
  domain?: string
  website?: string
  country?: string
  industry?: string
  industry_source?: string
  industry_confidence?: number
  industry_enrichment_status?: string
  industry_enrichment_error?: string
  enrichment_failure?: EnrichmentFailure
  emails_count: number
  relevance_score: number
  target_relevance_score?: number | null
  relevance_rating?: string
  evaluation_status?: string
  match_evaluation?: Record<string, unknown>
  company_profile?: Record<string, unknown>
  seen_count: number
  status: string
  last_seen_at?: string
  last_task_id?: string
  relevance_hint?: { level: 'high' | 'medium' | 'low' | 'unknown'; label: string; reason: string }
}

interface TaskContext {
  task_id?: string
  task_name?: string
  task_status?: string
  original_prompt?: string
  target_concepts?: Array<{ id?: string; source_text?: string; normalized_label?: string }>
  categories?: string[]
  countries?: string[]
}

interface TaskOption {
  id: string
  name: string
  mode?: string
}

interface BulkResultItem {
  candidate_id: string
  status: string
  reason_code?: string
  message?: string
  task_id?: string
}

const items = ref<Candidate[]>([])
const loading = ref(false)
const actionId = ref('')
const selectedIds = ref<string[]>([])
const statusFilter = ref('pending')
const route = useRoute()
const ALL_TASKS = '__all_tasks__'
const taskFilter = ref(typeof route.query.task_id === 'string' ? route.query.task_id : ALL_TASKS)
const taskOptions = ref<TaskOption[]>([])
const currentTask = ref<TaskContext | null>(null)
const page = ref(1)
const pageSize = 50
const total = ref(0)
const approvalVisible = ref(false)
const approvalCandidate = ref<Candidate | null>(null)
const approvalTitles = ref('Buyer\nHead of Buying\nSourcing Manager\nProcurement Manager')
const approvalContactLimit = ref(5)
const loadingTitles = ref(false)
const bulkVisible = ref(false)
const bulkApproving = ref(false)
const bulkResult = ref<{ approved: number; skipped: number; failed: number; items: BulkResultItem[] } | null>(null)
const tableRef = ref()

const taskFilterText = computed(() => {
  const parts: string[] = []
  if (currentTask.value?.categories?.length) parts.push(`品类：${currentTask.value.categories.join('、')}`)
  if (currentTask.value?.countries?.length) parts.push(`国家：${currentTask.value.countries.join('、')}`)
  return parts.join('；')
})
const selectedTaskId = computed(() => taskFilter.value === ALL_TASKS ? '' : taskFilter.value)
const isAllTasksView = computed(() => taskFilter.value === ALL_TASKS)

async function loadTaskOptions() {
  try {
    const { data } = await api.get('/search-tasks', { params: { page: 1, page_size: 100 } })
    taskOptions.value = (data.items || [])
      .filter((task: { mode?: string }) => task.mode === 'brand_discovery')
      .map((task: { id: string; name: string; mode?: string }) => ({
        id: task.id,
        name: task.name,
        mode: task.mode,
      }))
    if (taskFilter.value !== ALL_TASKS && !taskOptions.value.some(task => task.id === taskFilter.value)) {
      taskFilter.value = ALL_TASKS
    }
  } catch {
    taskOptions.value = []
  }
}

function listParams() {
  return {
    page: page.value,
    page_size: pageSize,
    status: statusFilter.value || undefined,
    sort_by: 'last_seen_at',
    sort_order: 'desc',
  }
}

async function load() {
  loading.value = true
  try {
    if (selectedTaskId.value) {
      const { data } = await api.get(`/search-tasks/${selectedTaskId.value}/discovery-candidates`, {
        params: listParams(),
      })
      items.value = data.items || []
      total.value = data.total || 0
      currentTask.value = data.task || null
    } else {
      const { data } = await api.get('/discovery-candidates', { params: listParams() })
      items.value = data.items || []
      total.value = data.total || 0
      currentTask.value = null
    }
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

function onFilterChange() {
  page.value = 1
  load()
}

function onTaskFilterChange() {
  if (isAllTasksView.value) statusFilter.value = 'pending'
  onFilterChange()
}

function onSelectionChange(rows: Candidate[]) {
  selectedIds.value = rows.map(row => row.id)
}

function emailDataStatus(candidate: Candidate): { label: string; type: 'success' | 'warning' | 'danger' } {
  if (!candidate.domain && !candidate.website) return { label: '无效：缺少官网', type: 'danger' }
  if ((candidate.emails_count || 0) > 0) {
    return { label: `有邮箱线索 ${candidate.emails_count} 条`, type: 'success' }
  }
  return { label: '暂未发现邮箱', type: 'warning' }
}

function relevanceHintType(level?: string): 'success' | 'warning' | 'danger' | 'info' {
  if (level === 'high') return 'success'
  if (level === 'medium') return 'warning'
  if (level === 'low') return 'danger'
  return 'info'
}

function splitTitles(value: string) {
  return [...new Set(value.split(/[\n,，;；]+/).map(item => item.trim()).filter(Boolean))]
}

function openApproval(candidate: Candidate) {
  if (!canApprove(candidate)) {
    ElMessage.warning('候选需要有效官网域名才能进行精准丰富')
    return
  }
  approvalCandidate.value = candidate
  approvalVisible.value = true
}

async function loadAllSystemTitles() {
  loadingTitles.value = true
  try {
    const { data } = await api.get('/system-settings')
    const dictionary = data.title_dictionary || {}
    const configured = [...(dictionary.p1 || []), ...(dictionary.p2 || []), ...(dictionary.p3 || [])]
    approvalTitles.value = [...new Set([...splitTitles(approvalTitles.value), ...configured])].join('\n')
    ElMessage.success(`已载入 ${splitTitles(approvalTitles.value).length} 个职位`)
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loadingTitles.value = false
  }
}

async function submitApproval() {
  const candidate = approvalCandidate.value
  if (!candidate) return
  const titles = splitTitles(approvalTitles.value)
  if (!titles.length) return ElMessage.warning('请至少填写一个目标职位')
  try {
    actionId.value = candidate.id
    await api.post(`/discovery-candidates/${candidate.id}/approve`, {
      task_id: selectedTaskId.value || candidate.last_task_id,
      target_titles: titles,
      contacts_limit_per_brand: approvalContactLimit.value,
    })
    approvalVisible.value = false
    ElMessage.success(`精准品牌任务已创建，将搜索 ${titles.length} 个职位`)
    await load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    actionId.value = ''
  }
}

function canApprove(candidate: Candidate) {
  return (
    ['pending', 'enrichment_failed'].includes(candidate.status) &&
    Boolean(candidate.domain || candidate.website) &&
    Boolean(selectedTaskId.value || candidate.last_task_id)
  )
}

function selectPageApprovable() {
  const approvable = items.value.filter(canApprove)
  if (!approvable.length) {
    ElMessage.info('本页没有具备官网域名的可丰富候选')
    return
  }
  tableRef.value?.clearSelection()
  approvable.forEach(row => tableRef.value?.toggleRowSelection(row, true))
  ElMessage.success(`已选择本页 ${approvable.length} 条可丰富候选`)
}

function openBulkApprove() {
  if (!selectedTaskId.value) {
    ElMessage.warning('请先选择搜索任务')
    return
  }
  bulkResult.value = null
  bulkVisible.value = true
}

function closeBulk() {
  bulkVisible.value = false
  bulkResult.value = null
}

function bulkStatusLabel(status: string) {
  const labels: Record<string, string> = { approved: '已发起', skipped: '已跳过', failed: '失败' }
  return labels[status] || status || '未知'
}

function nameOf(candidateId: string) {
  return items.value.find(item => item.id === candidateId)?.name || candidateId
}

async function submitBulkApprove() {
  const titles = splitTitles(approvalTitles.value)
  if (!titles.length) return ElMessage.warning('请至少填写一个目标职位')
  bulkApproving.value = true
  try {
    const { data } = await api.post('/discovery-candidates/bulk-approve', {
      task_id: selectedTaskId.value,
      candidate_ids: selectedIds.value,
      target_titles: titles,
      contacts_limit_per_brand: approvalContactLimit.value,
    })
    bulkResult.value = data
    const unsuccessful = (data.items || [])
      .filter((item: BulkResultItem) => item.status !== 'approved')
      .map((item: BulkResultItem) => item.candidate_id)
    await load()
    // Keep unsuccessful candidates selected so the user can act on them.
    tableRef.value?.clearSelection()
    items.value
      .filter(row => unsuccessful.includes(row.id))
      .forEach(row => tableRef.value?.toggleRowSelection(row, true))
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    bulkApproving.value = false
  }
}

async function reject(candidate: Candidate) {
  try {
    const { value } = await ElMessageBox.prompt('可选：填写拒绝原因', `拒绝 ${candidate.name}`, {
      confirmButtonText: '确认拒绝',
      cancelButtonText: '取消',
      inputPlaceholder: '例如：非目标行业、官网无效',
    })
    actionId.value = candidate.id
    await api.post(`/discovery-candidates/${candidate.id}/reject`, { reason: value || null })
    ElMessage.success('候选已拒绝，后续重复搜索将自动排除')
    await load()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error((error as Error).message)
  } finally {
    actionId.value = ''
  }
}

function statusLabel(status: string) {
  status = safeCandidateStatus(status)
  return {
    pending: '待审核',
    enriching: '精准丰富中',
    enrichment_failed: '丰富失败',
    promoted: '已转入客户库',
    rejected: '已拒绝',
  }[status] || status
}

function statusType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  if (status === 'promoted') return 'success'
  if (status === 'enrichment_failed' || status === 'rejected') return 'danger'
  if (status === 'enriching') return 'primary'
  if (status === 'pending') return 'warning'
  return 'info'
}

function formatDate(value?: string) {
  return value ? new Date(value).toLocaleString() : '-'
}

onMounted(async () => {
  await loadTaskOptions()
  await load()
})
</script>

<style scoped>
.task-banner {
  margin-bottom: 12px;
  padding: 12px 16px;
}

.task-banner-title {
  font-size: 14px;
  margin-bottom: 6px;
}

.task-banner-body {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 20px;
  font-size: 13px;
}

.table-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.relevance-header {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.score-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 1px solid var(--el-color-info);
  border-radius: 50%;
  color: var(--el-color-info);
  font-size: 11px;
  line-height: 1;
  cursor: help;
}

.score-rules {
  line-height: 1.8;
}

.score-rules strong {
  display: block;
  margin-bottom: 4px;
}

.score-note {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.score-value {
  display: flex;
  flex-direction: column;
  gap: 1px;
  line-height: 1.15;
}

.score-number {
  color: var(--el-color-primary);
  font-size: 18px;
  font-variant-numeric: tabular-nums;
  margin-left: 6px;
}

.score-value small {
  color: var(--el-text-color-secondary);
  font-size: 11px;
  white-space: nowrap;
}

.failure-text {
  color: var(--el-color-danger);
  font-size: 12px;
}

.title-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}

@media (max-width: 768px) {
  .table-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .table-toolbar .el-select,
  .table-toolbar .el-button {
    width: 100% !important;
    margin-left: 0;
  }

  .task-banner-body {
    overflow-wrap: anywhere;
  }

  :deep(.el-dialog) {
    width: calc(100vw - 32px) !important;
  }
}
</style>
