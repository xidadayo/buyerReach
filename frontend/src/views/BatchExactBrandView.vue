<template>
  <div class="page-heading">
    <h1 class="page-title">批量精准品牌</h1>
    <div>
      <el-button @click="downloadTemplate">下载模板</el-button>
    </div>
  </div>

  <!-- Step indicator -->
  <el-steps :active="activeStep" align-center finish-status="success" style="margin-bottom: 24px">
    <el-step title="上传文件" />
    <el-step title="校验预览" />
    <el-step title="统一设置" />
    <el-step title="执行结果" />
  </el-steps>

  <!-- Step 1: Upload -->
  <div v-if="activeStep === 0" class="panel">
    <el-upload
      drag
      :auto-upload="false"
      :limit="1"
      :on-change="handleFileChange"
      :on-remove="handleFileRemove"
      accept=".csv,.xlsx"
      :file-list="fileList"
    >
      <div style="font-size: 48px; color: #c0c4cc; margin-bottom: 8px">📁</div>
      <div class="el-upload__text">拖拽文件到此处或 <em>点击上传</em></div>
      <template #tip>
        <div class="el-upload__tip">
          支持 .csv（UTF-8 编码）和 .xlsx 文件，最多 {{ limits.maxRows }} 行 / {{ limits.maxFileSizeMb }} MB
        </div>
      </template>
    </el-upload>
    <div v-if="uploadError" style="margin-top: 12px">
      <el-alert :title="uploadError" type="error" :closable="false" show-icon />
    </div>
    <div style="margin-top: 18px; text-align: right">
      <el-button type="primary" :loading="previewing" :disabled="!selectedFile" @click="previewFile">
        下一步：校验预览
      </el-button>
    </div>
  </div>

  <!-- Step 2: Preview -->
  <div v-if="activeStep === 1" class="panel">
    <el-alert
      v-if="preview"
      :title="`共 ${preview.total_rows} 行：${preview.valid_rows} 可执行 · ${preview.warning_rows} 警告 · ${preview.invalid_rows} 错误 · ${preview.duplicate_rows} 重复`"
      :type="preview.invalid_rows > 0 ? 'warning' : 'success'"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    />
    <el-text size="small" type="info" style="margin-bottom: 12px; display: block">
      预览不会调用付费 Vendor。错误行和重复行默认跳过。
      <el-button type="primary" link size="small" @click="downloadErrorReport" v-if="errorCount > 0">下载错误报告</el-button>
    </el-text>
    <el-table :data="preview?.rows || []" max-height="420" size="small" stripe>
      <el-table-column prop="row_number" label="行号" width="70" />
      <el-table-column prop="company_name" label="公司名称" min-width="160" show-overflow-tooltip />
      <el-table-column prop="official_domain" label="域名" min-width="180" show-overflow-tooltip />
      <el-table-column prop="normalized_domain" label="规范化域名" min-width="180" show-overflow-tooltip />
      <el-table-column prop="country" label="国家" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.validation_status === 'valid' ? 'success' : row.validation_status === 'warning' ? 'warning' : 'danger'" size="small">
            {{ row.validation_status === 'valid' ? '有效' : row.validation_status === 'warning' ? '警告' : '错误' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="问题" min-width="180">
        <template #default="{ row }">
          <span v-for="err in row.validation_errors" :key="err" style="margin-right: 6px">
            <el-tag type="danger" size="small" effect="plain">{{ errorLabel(err) }}</el-tag>
          </span>
          <span v-for="w in row.warnings" :key="w" style="margin-right: 6px">
            <el-tag type="warning" size="small" effect="plain">{{ errorLabel(w) }}</el-tag>
          </span>
        </template>
      </el-table-column>
    </el-table>
    <div style="margin-top: 18px; display: flex; justify-content: space-between">
      <el-button @click="activeStep = 0">返回上传</el-button>
      <el-button type="primary" :disabled="!hasValidRows" @click="activeStep = 2">
        下一步：统一设置
      </el-button>
    </div>
  </div>

  <!-- Step 3: Settings -->
  <div v-if="activeStep === 2" class="panel">
    <el-form label-width="140px" :model="settings">
      <el-form-item label="任务名称" required>
        <el-input v-model="settings.name" placeholder="批量精准品牌任务" maxlength="255" />
      </el-form-item>
      <el-form-item label="数据来源" required>
        <el-radio-group v-model="settings.vendorMode">
          <el-radio-button value="apollo_only" :disabled="!vendorAvailable('apollo')">仅 Apollo</el-radio-button>
          <el-radio-button value="hunter_only" :disabled="!vendorAvailable('hunter')">仅 Hunter</el-radio-button>
          <el-radio-button value="apollo_hunter" :disabled="!vendorAvailable('apollo') || !vendorAvailable('hunter')">Apollo + Hunter</el-radio-button>
        </el-radio-group>
        <el-text size="small" type="info" style="margin-top: 4px; display: block">
          双选时 Apollo 和 Hunter 各自独立执行完整流程，不互相回退。
        </el-text>
      </el-form-item>
      <el-form-item label="目标职位" required>
        <el-input v-model="settings.targetTitles" placeholder="多个职位用逗号分隔" />
      </el-form-item>
      <el-form-item label="联系人上限">
        <el-input-number v-model="settings.contactsLimit" :min="1" :max="50" controls-position="right" />
      </el-form-item>
      <el-form-item label="可靠邮箱策略">
        <el-switch v-model="settings.reliableEmailOnly" active-text="仅可靠邮箱" inactive-text="包含待复核" />
      </el-form-item>

      <el-divider />
      <el-collapse>
        <el-collapse-item title="高级设置" name="advanced">
          <el-form-item label="跳过已有品牌">
            <el-switch v-model="settings.skipExistingBrands" />
          </el-form-item>
          <el-form-item label="预算上限（USD）">
            <el-input-number v-model="settings.budgetLimit" :min="0" :step="10" controls-position="right" placeholder="无上限" />
          </el-form-item>
          <el-form-item label="最大并发数">
            <el-input-number v-model="settings.maxConcurrency" :min="1" :max="10" controls-position="right" />
          </el-form-item>
          <el-form-item label="每目标重试上限">
            <el-input-number v-model="settings.retryLimit" :min="1" :max="10" controls-position="right" />
          </el-form-item>
        </el-collapse-item>
      </el-collapse>

      <el-divider />
      <el-descriptions title="确认汇总" :column="2" border>
        <el-descriptions-item label="有效公司数">{{ validCompanyCount }}</el-descriptions-item>
        <el-descriptions-item label="数据来源">{{ settings.vendorMode === 'apollo_hunter' ? 'Apollo + Hunter' : settings.vendorMode === 'apollo_only' ? 'Apollo' : 'Hunter' }}</el-descriptions-item>
        <el-descriptions-item label="Vendor 管线数">{{ settings.vendorMode === 'apollo_hunter' ? 2 : 1 }}</el-descriptions-item>
        <el-descriptions-item label="每公司联系人上限">{{ settings.contactsLimit }}</el-descriptions-item>
        <el-descriptions-item label="理论最大联系人处理量（上限）">{{ validCompanyCount * settings.contactsLimit * (settings.vendorMode === 'apollo_hunter' ? 2 : 1) }}</el-descriptions-item>
        <el-descriptions-item label="预算上限">{{ settings.budgetLimit ? `$${settings.budgetLimit}` : '无限制' }}</el-descriptions-item>
      </el-descriptions>
      <el-text size="small" type="warning" style="margin-top: 8px; display: block">
        以上联系人处理量为理论上限，非精确费用估算。实际数量取决于搜索结果。
      </el-text>
    </el-form>
    <div style="margin-top: 18px; display: flex; justify-content: space-between">
      <el-button @click="activeStep = 1">返回预览</el-button>
      <el-button type="primary" :loading="confirming" :disabled="!settings.name.trim() || !settings.targetTitles.trim()" @click="confirmBatch">
        确认并创建任务
      </el-button>
    </div>
  </div>

  <!-- Step 4: Execution Results -->
  <div v-if="activeStep === 3" class="panel">
    <el-alert
      v-if="confirmResult"
      :title="confirmResult.already_confirmed ? '任务已存在（幂等确认）' : '任务已创建并开始执行'"
      :type="confirmResult.already_confirmed ? 'warning' : 'success'"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    />
    <el-descriptions v-if="confirmResult" :column="2" border style="margin-bottom: 16px">
      <el-descriptions-item label="父任务 ID">{{ confirmResult.parent_task_id }}</el-descriptions-item>
      <el-descriptions-item label="任务名称">{{ confirmResult.parent_task_name }}</el-descriptions-item>
      <el-descriptions-item label="目标数量">{{ confirmResult.target_count }}</el-descriptions-item>
      <el-descriptions-item label="数据来源">{{ confirmResult.vendors?.join(' + ') }}</el-descriptions-item>
    </el-descriptions>

    <!-- Target progress -->
    <div v-if="targetSummary" style="margin-bottom: 16px">
      <el-row :gutter="12">
        <el-col :span="4" v-for="stat in targetStats" :key="stat.label">
          <el-statistic :title="stat.label" :value="stat.value">
            <template #suffix v-if="stat.suffix">
              <el-tag :type="stat.tagType" size="small">{{ stat.suffix }}</el-tag>
            </template>
          </el-statistic>
        </el-col>
      </el-row>
    </div>

    <!-- Target table -->
    <el-table :data="targets?.items || []" max-height="400" size="small" stripe v-loading="targetsLoading">
      <el-table-column prop="row_number" label="#" width="55" />
      <el-table-column prop="company_name" label="公司" min-width="150" show-overflow-tooltip />
      <el-table-column prop="normalized_domain" label="域名" min-width="160" show-overflow-tooltip />
      <el-table-column label="执行状态" width="110">
        <template #default="{ row }">
          <el-tag :type="executionStatusType(row.execution_status)" size="small">
            {{ executionStatusLabel(row.execution_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="contact_count" label="联系人" width="80" />
      <el-table-column prop="reliable_email_count" label="可靠邮箱" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.reliable_email_count > 0" type="success" size="small">{{ row.reliable_email_count }}</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="error_message" label="错误" min-width="160" show-overflow-tooltip />
    </el-table>

    <!-- Pagination + filters -->
    <div style="margin-top: 12px; display: flex; justify-content: space-between; align-items: center">
      <div>
        <el-radio-group v-model="targetFilter" size="small" @change="loadTargets">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="completed">已完成</el-radio-button>
          <el-radio-button value="running">执行中</el-radio-button>
          <el-radio-button value="no_match">未找到</el-radio-button>
          <el-radio-button value="failed">失败</el-radio-button>
        </el-radio-group>
      </div>
      <el-pagination
        v-if="targets && targets.total > pageSize"
        v-model:current-page="targetPage"
        :page-size="pageSize"
        :total="targets.total"
        layout="prev, pager, next"
        size="small"
        @current-change="loadTargets"
      />
    </div>

    <!-- Actions -->
    <div style="margin-top: 18px; display: flex; gap: 8px; flex-wrap: wrap">
      <el-button @click="retryFailedTargets" :disabled="!hasFailedTargets" type="warning">
        仅重试失败项
      </el-button>
      <el-button @click="exportReliableEmails" type="success" plain>
        导出可靠邮箱
      </el-button>
      <el-button @click="exportErrorReport" plain>
        导出错误报告
      </el-button>
      <el-button @click="$router.push('/tasks')" type="primary">
        返回任务列表
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'

// ── State ──────────────────────────────────────────────────────────────────
const activeStep = ref(0)
const fileList = ref<any[]>([])
const selectedFile = ref<File | null>(null)
const uploadError = ref('')
const previewing = ref(false)
const confirming = ref(false)
const preview = ref<any>(null)
const confirmResult = ref<any>(null)
const targets = ref<any>(null)
const targetsLoading = ref(false)
const targetFilter = ref('')
const targetPage = ref(1)
const pageSize = 50

const limits = { maxRows: 5000, maxFileSizeMb: 10 }

const settings = reactive({
  name: '',
  vendorMode: 'apollo_hunter' as string,
  targetTitles: '',
  contactsLimit: 5,
  reliableEmailOnly: true,
  skipExistingBrands: false,
  budgetLimit: null as number | null,
  maxConcurrency: 3,
  retryLimit: 3,
})

const vendorCapabilities = ref<Record<string, any>>({})

// ── Computed ───────────────────────────────────────────────────────────────
const hasValidRows = computed(() => {
  return (preview.value?.valid_rows || 0) + (preview.value?.warning_rows || 0) > 0
})
const errorCount = computed(() => preview.value?.invalid_rows || 0)
const validCompanyCount = computed(() => {
  return (preview.value?.valid_rows || 0) + (preview.value?.warning_rows || 0)
})
const hasFailedTargets = computed(() => {
  return (targets.value?.items || []).some(
    (t: any) => ['failed', 'retryable'].includes(t.execution_status)
  )
})

const targetSummary = computed(() => {
  if (targets.value?.summary) {
    const summary = targets.value.summary
    return {
      total: summary.total,
      completed: summary.completed,
      running: summary.running,
      pending: summary.pending,
      noMatch: summary.no_match,
      failed: summary.failed,
      reliableEmails: summary.reliable_emails,
    }
  }
  if (!targets.value?.items) return null
  const items = targets.value.items
  return {
    total: items.length,
    completed: items.filter((t: any) => t.execution_status === 'completed').length,
    running: items.filter((t: any) => t.execution_status === 'running').length,
    pending: items.filter((t: any) => ['pending', 'queued'].includes(t.execution_status)).length,
    noMatch: items.filter((t: any) => t.execution_status === 'no_match').length,
    failed: items.filter((t: any) => ['failed', 'retryable'].includes(t.execution_status)).length,
    reliableEmails: items.reduce((sum: number, t: any) => sum + (t.reliable_email_count || 0), 0),
  }
})

const targetStats = computed(() => {
  const s = targetSummary.value
  if (!s) return []
  return [
    { label: '总计', value: s.total, tagType: 'info' as const },
    { label: '已完成', value: s.completed, tagType: 'success' as const, suffix: s.completed > 0 ? String(s.completed) : undefined },
    { label: '执行中', value: s.running, tagType: 'warning' as const },
    { label: '未找到', value: s.noMatch, tagType: 'info' as const },
    { label: '失败', value: s.failed, tagType: 'danger' as const },
    { label: '可靠邮箱', value: s.reliableEmails, tagType: 'success' as const, suffix: s.reliableEmails > 0 ? String(s.reliableEmails) : undefined },
  ]
})

// ── Methods ────────────────────────────────────────────────────────────────
function vendorAvailable(vendor: string) {
  return vendorCapabilities.value[vendor]?.available !== false
}

async function loadVendorCapabilities() {
  try {
    const { data } = await api.get('/vendor-capabilities', { params: { task_mode: 'exact_brand' } })
    vendorCapabilities.value = Object.fromEntries(
      (Array.isArray(data) ? data : []).map((item: any) => [item.vendor, item])
    )
  } catch {
    vendorCapabilities.value = {}
  }
}

async function loadTaskDefaults() {
  try {
    const { data } = await api.get('/task-defaults')
    settings.targetTitles = Array.isArray(data?.target_titles) ? data.target_titles.join(',') : ''
    settings.contactsLimit = Number(data?.contacts_limit_per_brand || 5)
  } catch {
    settings.targetTitles = ''
  }
}

function handleFileChange(file: any) {
  selectedFile.value = file.raw
  uploadError.value = ''
}

function handleFileRemove() {
  selectedFile.value = null
  uploadError.value = ''
}

async function downloadTemplate() {
  try {
    const response = await api.get('/batch-exact-brand/template', { responseType: 'blob' })
    const url = URL.createObjectURL(response.data)
    const a = document.createElement('a')
    a.href = url
    a.download = 'exact-brand-import-v1.csv'
    a.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error('模板下载失败')
  }
}

async function previewFile() {
  if (!selectedFile.value) return
  previewing.value = true
  uploadError.value = ''
  try {
    const form = new FormData()
    form.append('file', selectedFile.value)
    const { data } = await api.post('/batch-exact-brand/preview', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    preview.value = data
    settings.name = selectedFile.value.name.replace(/\.[^.]+$/, '') + ' 批量精准品牌'
    activeStep.value = 1
  } catch (error: any) {
    uploadError.value = error.response?.data?.detail || error.message || '预览失败'
  } finally {
    previewing.value = false
  }
}

function downloadErrorReport() {
  if (!preview.value?.rows) return
  const errorRows = preview.value.rows.filter((r: any) => r.validation_status === 'error')
  if (!errorRows.length) return
  const csv = ['row_number,company_name,official_domain,errors']
  for (const r of errorRows) {
    csv.push(`${r.row_number},"${r.company_name}","${r.official_domain}","${(r.validation_errors || []).join('; ')}"`)
  }
  const blob = new Blob(['﻿' + csv.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'batch-import-errors.csv'
  a.click()
  URL.revokeObjectURL(url)
}

async function confirmBatch() {
  confirming.value = true
  try {
    // First create the batch import
    const form = new FormData()
    form.append('file', selectedFile.value!)
    const { data: importData } = await api.post('/batch-exact-brand/imports', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    // Then confirm it
    const selectedVendors = settings.vendorMode === 'apollo_only'
      ? ['apollo'] : settings.vendorMode === 'hunter_only'
      ? ['hunter'] : ['apollo', 'hunter']

    const { data } = await api.post(`/batch-exact-brand/imports/${importData.id}/confirm`, {
      name: settings.name.trim(),
      selected_vendors: selectedVendors,
      target_titles: settings.targetTitles.split(/[,，、;；\n]+/).map((s: string) => s.trim()).filter(Boolean),
      contacts_limit_per_brand: settings.contactsLimit,
      reliable_email_only: settings.reliableEmailOnly,
      skip_existing_brands: settings.skipExistingBrands,
      budget_limit: settings.budgetLimit,
      max_concurrency: settings.maxConcurrency,
      retry_limit_per_target: settings.retryLimit,
    })
    confirmResult.value = data
    activeStep.value = 3

    // Start loading targets
    if (data.parent_task_id) {
      loadTargets()
    }
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || error.message || '确认失败')
  } finally {
    confirming.value = false
  }
}

async function loadTargets() {
  if (!confirmResult.value?.parent_task_id) return
  targetsLoading.value = true
  try {
    const params: any = { page: targetPage.value, page_size: pageSize }
    if (targetFilter.value) params.status = targetFilter.value
    const { data } = await api.get(`/search-tasks/${confirmResult.value.parent_task_id}/targets`, { params })
    targets.value = data
  } catch {
    // ignore
  } finally {
    targetsLoading.value = false
  }
}

async function retryFailedTargets() {
  if (!confirmResult.value?.parent_task_id) return
  const failedIds = (targets.value?.items || [])
    .filter((t: any) => ['failed', 'retryable'].includes(t.execution_status))
    .map((t: any) => t.id)
  if (!failedIds.length) return ElMessage.warning('没有可重试的失败项')
  try {
    const { data } = await api.post(
      `/search-tasks/${confirmResult.value.parent_task_id}/targets/retry`,
      { target_ids: failedIds }
    )
    ElMessage.success(`已重新排队 ${data.retried} 个目标`)
    await loadTargets()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || error.message || '重试失败')
  }
}

async function exportReliableEmails() {
  if (!confirmResult.value?.parent_task_id) return
  try {
    const response = await api.get(
      `/search-tasks/${confirmResult.value.parent_task_id}/targets/export.csv`,
      { responseType: 'blob' }
    )
    const url = URL.createObjectURL(response.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `reliable-emails-${confirmResult.value.parent_task_id}.csv`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('导出失败')
  }
}

async function exportErrorReport() {
  if (!confirmResult.value?.parent_task_id) return
  try {
    const response = await api.get(
      `/search-tasks/${confirmResult.value.parent_task_id}/targets/errors.csv`,
      { responseType: 'blob' }
    )
    const url = URL.createObjectURL(response.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `batch-errors-${confirmResult.value.parent_task_id}.csv`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('导出失败')
  }
}

// ── Labels ─────────────────────────────────────────────────────────────────
function errorLabel(code: string) {
  const map: Record<string, string> = {
    MISSING_COMPANY_NAME: '缺少公司名',
    MISSING_DOMAIN: '缺少域名',
    INVALID_DOMAIN: '无效域名',
    DUPLICATE_DOMAIN: '域名重复',
    COMPANY_NAME_TOO_SHORT: '公司名过短',
    COMPANY_NAME_TOO_LONG: '公司名过长',
    FORMULA_INJECTION: '公式注入',
  }
  return map[code] || code
}

function executionStatusLabel(status: string) {
  const map: Record<string, string> = {
    pending: '等待中', queued: '排队中', running: '执行中',
    completed: '已完成', no_match: '未找到', partial: '部分完成',
    retryable: '可重试', failed: '失败', cancelled: '已取消',
  }
  return map[status] || status
}

function executionStatusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'running') return 'warning'
  if (status === 'failed' || status === 'retryable') return 'danger'
  if (status === 'no_match') return 'info'
  return ''
}

// ── Lifecycle ──────────────────────────────────────────────────────────────
let timer: number | undefined
onMounted(() => {
  loadVendorCapabilities()
  loadTaskDefaults()
  // Poll for target updates when on step 4
  timer = window.setInterval(() => {
    if (activeStep.value === 3 && confirmResult.value?.parent_task_id) {
      loadTargets()
    }
  }, 5000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>
