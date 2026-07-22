<template>
  <div class="page-heading">
    <h1 class="page-title">邮箱池</h1>
    <el-space>
      <el-button :disabled="!selectedRows.length" :loading="exporting" @click="exportSelected">批量导出 {{ selectedRows.length ? `(${selectedRows.length})` : '' }}</el-button>
      <el-button type="danger" :disabled="!selectedRows.length" :loading="archiving" @click="archiveSelected">批量删除 {{ selectedRows.length ? `(${selectedRows.length})` : '' }}</el-button>
      <el-button type="primary" @click="openDialog">新增邮箱</el-button>
    </el-space>
  </div>
  <div class="filter-status">
    <span>真实性筛选</span>
    <el-select v-model="authenticityFilter" clearable placeholder="全部邮箱" style="width: 160px">
      <el-option label="已验证" value="verified" />
      <el-option label="较可信" value="probable" />
      <el-option label="有风险" value="risky" />
      <el-option label="无效" value="invalid" />
      <el-option label="未验证" value="unverified" />
    </el-select>
    <el-button type="success" plain @click="authenticityFilter = 'verified'">只看可用邮箱</el-button>
  </div>
  <div v-if="contactId" class="filter-status">
    <el-tag type="info">已按联系人筛选邮箱</el-tag>
    <el-button link type="primary" @click="clearAssociationFilter">查看全部邮箱</el-button>
  </div>
  <div v-else-if="brandId" class="filter-status">
    <el-tag type="info">已按品牌筛选：{{ brandName || '当前品牌' }}</el-tag>
    <el-button link type="primary" @click="clearAssociationFilter">查看全部邮箱</el-button>
  </div>
  <div v-if="lastExport" class="export-status">
    <el-tag type="success">导出完成</el-tag>
    <span>已生成 {{ lastExport.count }} 条邮箱的文件：{{ lastExport.filename }}</span>
    <el-button link type="primary" @click="downloadLastExport">再次下载</el-button>
  </div>
  <div class="panel">
    <EntityTable
      :key="emailEndpoint"
      ref="table"
      :endpoint="emailEndpoint"
      :columns="columns"
      table-max-height="calc(100vh - 320px)"
      scrollbar-always-on
      selectable
      @selection-change="onSelectionChange"
    >
      <template #cell-status="{ value, row }">
        <el-tooltip :content="emailStatusHint(row)" :disabled="!emailStatusHint(row)" placement="top">
          <el-tag :type="emailTag(value)">{{ emailStatusLabel(value, row) }}</el-tag>
        </el-tooltip>
      </template>
      <template #cell-verification_server="{ row }"><el-tag type="info">{{ verificationServerLabel(row) }}</el-tag></template>
      <template #cell-pool="{ value }"><span>{{ poolLabel(value) }}</span></template>
      <template #cell-authenticity_level="{ value }"><el-tag :type="authenticityTag(value)">{{ authenticityLabel(value) }}</el-tag></template>
      <template #cell-confidence_score="{ value }"><el-progress :percentage="Number(value || 0)" :stroke-width="8" /></template>
      <template #cell-domain_matches_brand="{ value }"><el-tag :type="value ? 'success' : 'warning'">{{ value ? '匹配' : '未匹配' }}</el-tag></template>
      <template #actions="{ row }">
        <el-button size="small" @click="showAuthenticity(row.id)">真实性</el-button>
        <el-button size="small" :loading="workingId === row.id" @click="verify(row.id)">验证</el-button>
        <el-dropdown v-if="row.pool === 'manual_review'" @command="(command: string) => review(row.id, command)">
          <el-button size="small" type="primary">审核</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="approve">通过</el-dropdown-item>
              <el-dropdown-item command="reject">拒绝</el-dropdown-item>
              <el-dropdown-item command="suppress" divided>禁止发送</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </template>
    </EntityTable>
  </div>

  <el-dialog v-model="dialogVisible" title="新增邮箱" width="520px">
    <el-form label-width="100px" :model="form">
      <el-form-item label="联系人">
        <el-select v-model="form.contact_id" clearable filterable style="width: 100%">
          <el-option v-for="contact in contacts" :key="contact.id" :label="contact.full_name" :value="contact.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="所属品牌">
        <el-select v-model="form.brand_id" clearable filterable style="width: 100%">
          <el-option v-for="brand in brands" :key="brand.id" :label="brand.name" :value="brand.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="邮箱地址" required><el-input v-model="form.address" /></el-form-item>
      <el-form-item label="邮箱类型">
        <el-select v-model="form.type" style="width: 100%">
          <el-option label="个人邮箱" value="personal" />
          <el-option label="通用邮箱" value="generic" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存并验证</el-button>
    </template>
  </el-dialog>

  <el-drawer v-model="detailVisible" title="邮箱真实性报告" size="620px">
    <div v-loading="detailLoading">
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="邮箱" :span="2">{{ detail.address }}</el-descriptions-item>
          <el-descriptions-item label="真实性"><el-tag :type="authenticityTag(detail.authenticity_level)">{{ authenticityLabel(detail.authenticity_level) }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="综合可信度">{{ detail.confidence_score }} / 100</el-descriptions-item>
          <el-descriptions-item label="可投递性">{{ detail.deliverability_score }} / 100</el-descriptions-item>
          <el-descriptions-item label="身份归属">{{ detail.identity_score }} / 100</el-descriptions-item>
          <el-descriptions-item label="来源证据">{{ detail.evidence_score }} / 100</el-descriptions-item>
          <el-descriptions-item label="企业域名">{{ detail.domain_matches_brand ? '匹配' : '未匹配' }}</el-descriptions-item>
          <el-descriptions-item label="Catch-all">{{ detail.is_catch_all ? '是' : '否' }}</el-descriptions-item>
          <el-descriptions-item label="一次性邮箱">{{ detail.is_disposable ? '是' : '否' }}</el-descriptions-item>
          <el-descriptions-item label="最近验证" :span="2">{{ detail.last_verified_at || '-' }}</el-descriptions-item>
        </el-descriptions>
        <h3>证据链</h3>
        <el-timeline v-if="detail.evidence?.length">
          <el-timeline-item v-for="item in detail.evidence" :key="item.id" :timestamp="item.created_at" placement="top">
            <strong>{{ item.title || item.source_type }}</strong>
            <div>{{ item.url || item.provider || '-' }}</div>
            <div>证据置信度：{{ item.confidence || 0 }}</div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无来源证据" />
      </template>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import EntityTable, { type TableColumn } from '../components/EntityTable.vue'
import { api } from '../api/client'

const table = ref<InstanceType<typeof EntityTable>>()
const route = useRoute()
const router = useRouter()
const contactId = computed(() => typeof route.query.contact_id === 'string' ? route.query.contact_id : '')
const brandId = computed(() => typeof route.query.brand_id === 'string' ? route.query.brand_id : '')
const brandName = computed(() => typeof route.query.brand_name === 'string' ? route.query.brand_name : '')
const authenticityFilter = ref('')
const emailEndpoint = computed(() => {
  const params = new URLSearchParams()
  if (contactId.value) params.set('contact_id', contactId.value)
  else if (brandId.value) params.set('brand_id', brandId.value)
  if (authenticityFilter.value) params.set('authenticity_level', authenticityFilter.value)
  const query = params.toString()
  return query ? `/emails?${query}` : '/emails'
})
const dialogVisible = ref(false)
const saving = ref(false)
const workingId = ref('')
const archiving = ref(false)
const exporting = ref(false)
const contacts = ref<any[]>([])
const brands = ref<any[]>([])
const selectedRows = ref<Record<string, any>[]>([])
const lastExport = ref<{ count: number; filename: string; url: string } | null>(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<Record<string, any> | null>(null)
const form = reactive({ contact_id: '', brand_id: '', address: '', type: 'personal' })
const columns: TableColumn[] = [
  { key: 'address', label: '邮箱地址', width: 220 },
  { key: 'contact_name', label: '联系人', width: 160 },
  { key: 'brand_name', label: '所属品牌', width: 160 },
  { key: 'status', label: '验证状态', width: 220 },
  { key: 'verification_server', label: '最终结论来源', width: 230 },
  { key: 'authenticity_level', label: '真实性', width: 110 },
  { key: 'confidence_score', label: '综合可信度', width: 150 },
  { key: 'deliverability_score', label: '可投递', width: 90 },
  { key: 'identity_score', label: '身份归属', width: 90 },
  { key: 'domain_matches_brand', label: '企业域名', width: 100 },
  { key: 'pool', label: '邮箱池', width: 140 },
  { key: 'score', label: '评分', width: 90 },
  { key: 'provider', label: '来源', width: 140 },
  { key: 'created_at', label: '创建时间', width: 180 },
]

async function openDialog() {
  try {
    const [contactResponse, brandResponse] = await Promise.all([
      api.get('/contacts', { params: { page_size: 200 } }),
      api.get('/brands', { params: { page_size: 200 } }),
    ])
    contacts.value = contactResponse.data.items || []
    brands.value = brandResponse.data.items || []
    dialogVisible.value = true
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function save() {
  if (!form.address.trim()) return ElMessage.warning('请填写邮箱地址')
  saving.value = true
  try {
    const { data } = await api.post('/emails', { ...form, contact_id: form.contact_id || null, brand_id: form.brand_id || null })
    await api.post('/emails/verify', { email_id: data.id })
    Object.assign(form, { contact_id: '', brand_id: '', address: '', type: 'personal' })
    dialogVisible.value = false
    ElMessage.success('邮箱已保存并完成验证')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    saving.value = false
  }
}

async function verify(id: string) {
  workingId.value = id
  try {
    await api.post('/emails/verify', { email_id: id })
    ElMessage.success('验证完成')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    workingId.value = ''
  }
}

async function showAuthenticity(id: string) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = (await api.get(`/emails/${id}/authenticity`)).data
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    detailLoading.value = false
  }
}

async function review(id: string, decision: string) {
  try {
    await api.post(`/emails/${id}/review`, { decision })
    ElMessage.success('审核结果已保存')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

function onSelectionChange(rows: Record<string, any>[]) {
  selectedRows.value = rows
}

function clearAssociationFilter() {
  const query = { ...route.query }
  delete query.contact_id
  delete query.brand_id
  delete query.brand_name
  router.replace({ query: { ...query, tab: 'emails' } })
}

async function archiveSelected() {
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${selectedRows.value.length} 条邮箱吗？记录将移至归档。`, '批量删除邮箱', { type: 'warning' })
    archiving.value = true
    const { data } = await api.post('/emails/bulk-archive', { ids: selectedRows.value.map((row) => row.id) })
    selectedRows.value = []
    ElMessage.success(`已归档 ${data.archived} 条邮箱`)
    await table.value?.load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message)
  } finally {
    archiving.value = false
  }
}

async function exportSelected() {
  exporting.value = true
  try {
    const response = await api.post('/emails/export', { ids: selectedRows.value.map((row) => row.id) }, { responseType: 'blob' })
    if (lastExport.value) URL.revokeObjectURL(lastExport.value.url)
    const filename = response.headers['content-disposition']?.match(/filename="?([^";]+)"?/)?.[1] || 'buyerreach-emails-selected.csv'
    lastExport.value = {
      count: Number(response.headers['x-exported-count'] || selectedRows.value.length),
      filename,
      url: URL.createObjectURL(response.data),
    }
    downloadLastExport()
    ElMessage.success(`已导出 ${lastExport.value.count} 条邮箱`)
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    exporting.value = false
  }
}

function downloadLastExport() {
  if (!lastExport.value) return
  const link = document.createElement('a')
  link.href = lastExport.value.url
  link.download = lastExport.value.filename
  link.click()
}

onBeforeUnmount(() => {
  if (lastExport.value) URL.revokeObjectURL(lastExport.value.url)
})

function emailTag(value: string) {
  if (value === 'valid') return 'success'
  if (value === 'invalid' || value === 'do_not_contact') return 'danger'
  if (value === 'risky' || value === 'unknown') return 'warning'
  return 'info'
}
function emailStatusLabel(value: string, row?: Record<string, any>) {
  if (value === 'risky' && row?.is_catch_all) return 'Catch-all：具体地址无法确认'
  return ({
    valid: 'SMTP 已接受，可投递',
    risky: '存在风险，需人工确认',
    invalid: '已确认不可投递',
    disposable: '一次性邮箱',
    do_not_contact: '禁止联系',
    unknown: '未获得邮箱级结论',
    raw: '未验证',
    candidate: '待验证',
  } as Record<string, string>)[value] || value
}
function emailStatusHint(row?: Record<string, any>) {
  return row?.verification_summary?.third_party_review?.message || ''
}
function verificationServerLabel(row?: Record<string, any>) {
  const summary = row?.verification_summary || {}
  const provider = String(summary.provider || '')
  if (provider.startsWith('aftership_local')) return '本地验证服务器'
  if (provider.startsWith('zerobounce')) return 'ZeroBounce'
  if (provider.startsWith('hunter')) return 'Hunter'
  if (provider === 'domain_deliverability') {
    const localAttempted = Array.isArray(summary.provider_errors)
      && summary.provider_errors.some((item: unknown) => String(item).startsWith('aftership_local'))
    return localAttempted ? '本地无明确结论（仅域名检查）' : '仅域名检查'
  }
  if (provider === 'blacklist') return '系统黑名单'
  return provider || '未记录'
}
function authenticityTag(value: string) {
  if (value === 'verified') return 'success'
  if (value === 'probable') return 'primary'
  if (value === 'invalid') return 'danger'
  if (value === 'risky') return 'warning'
  return 'info'
}
function authenticityLabel(value: string) {
  return ({ verified: '已验证', probable: '较可信', risky: '有风险', invalid: '无效', unverified: '未验证' } as Record<string, string>)[value] || value
}
function poolLabel(value: string) {
  return ({ raw: '原始池', pending_verification: '待验证池', manual_review: '人工审核池', valid: '有效邮箱池', invalid: '无效邮箱池', suppressed: '禁止发送池' } as Record<string, string>)[value] || value
}
</script>

<style scoped>
.filter-status, .export-status { display: flex; align-items: center; gap: 10px; margin: 0 0 16px; color: var(--el-text-color-regular); }
</style>
