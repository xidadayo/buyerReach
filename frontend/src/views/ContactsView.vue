<template>
  <div class="page-heading">
    <h1 class="page-title">联系人</h1>
    <el-space>
      <el-button :disabled="!selectedRows.length" :loading="exporting" @click="exportSelected">批量导出 {{ selectedRows.length ? `(${selectedRows.length})` : '' }}</el-button>
      <el-button type="danger" :disabled="!selectedRows.length" :loading="archiving" @click="archiveSelected">批量删除 {{ selectedRows.length ? `(${selectedRows.length})` : '' }}</el-button>
      <el-button type="primary" @click="openDialog">新增联系人</el-button>
    </el-space>
  </div>
  <div v-if="lastExport" class="export-status">
    <el-tag type="success">导出完成</el-tag>
    <span>已生成 {{ lastExport.count }} 条联系人的文件：{{ lastExport.filename }}</span>
    <el-button link type="primary" @click="downloadLastExport">再次下载</el-button>
  </div>
  <div class="panel">
    <EntityTable ref="table" endpoint="/contacts" :columns="columns" selectable @selection-change="onSelectionChange">
      <template #cell-linkedin_url="{ value }">
        <a v-if="value" :href="value" target="_blank" rel="noreferrer">LinkedIn</a><span v-else>-</span>
      </template>
      <template #cell-email_count="{ value, row }">
        <el-button link type="primary" :disabled="!value" @click="viewEmails(row)">{{ value || 0 }} 个</el-button>
      </template>
      <template #cell-status="{ value }">
        <el-tag size="small" :type="contactStatusType(value)">{{ contactStatusLabel(value) }}</el-tag>
      </template>
      <template #actions="{ row }">
        <el-button size="small" :disabled="!row.email_count" @click="viewEmails(row)">查看邮箱</el-button>
      </template>
    </EntityTable>
  </div>

  <el-dialog v-model="dialogVisible" title="新增联系人" width="560px">
    <el-form label-width="100px" :model="form">
      <el-form-item label="所属品牌">
        <el-select v-model="form.brand_id" clearable filterable style="width: 100%">
          <el-option v-for="brand in brands" :key="brand.id" :label="brand.name" :value="brand.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="名字" required><el-input v-model="form.first_name" /></el-form-item>
      <el-form-item label="姓氏"><el-input v-model="form.last_name" /></el-form-item>
      <el-form-item label="职位" required><el-input v-model="form.title" /></el-form-item>
      <el-form-item label="LinkedIn"><el-input v-model="form.linkedin_url" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import EntityTable, { type TableColumn } from '../components/EntityTable.vue'
import { api } from '../api/client'

const table = ref<InstanceType<typeof EntityTable>>()
const router = useRouter()
const dialogVisible = ref(false)
const saving = ref(false)
const archiving = ref(false)
const exporting = ref(false)
const brands = ref<any[]>([])
const selectedRows = ref<Record<string, any>[]>([])
const lastExport = ref<{ count: number; filename: string; url: string } | null>(null)
const form = reactive({ brand_id: '', first_name: '', last_name: '', title: '', linkedin_url: '' })
const columns: TableColumn[] = [
  { key: 'full_name', label: '姓名', width: 160 },
  { key: 'title', label: '职位', width: 180 },
  { key: 'brand_name', label: '品牌', width: 160 },
  { key: 'email_count', label: '关联邮箱', width: 120 },
  { key: 'linkedin_url', label: 'LinkedIn', width: 110 },
  { key: 'status', label: '有效性', width: 130 },
  { key: 'created_at', label: '创建时间', width: 180 },
]

function contactStatusLabel(status: string) {
  if (status === 'valid') return '有效联系人'
  if (status === 'pending_verification') return '邮箱待验证'
  if (status === 'pending_review') return '邮箱待复核'
  return '无有效邮箱'
}

function contactStatusType(status: string) {
  if (status === 'valid') return 'success'
  if (status === 'pending_verification' || status === 'pending_review') return 'warning'
  return 'danger'
}

async function openDialog() {
  try {
    brands.value = (await api.get('/brands', { params: { page_size: 200 } })).data.items || []
    dialogVisible.value = true
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function save() {
  if (!form.first_name.trim() || !form.title.trim()) return ElMessage.warning('请填写姓名和职位')
  saving.value = true
  try {
    await api.post('/contacts', { ...form, brand_id: form.brand_id || null, linkedin_url: form.linkedin_url || null })
    Object.assign(form, { brand_id: '', first_name: '', last_name: '', title: '', linkedin_url: '' })
    dialogVisible.value = false
    ElMessage.success('联系人已保存')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    saving.value = false
  }
}

function onSelectionChange(rows: Record<string, any>[]) {
  selectedRows.value = rows
}

function viewEmails(row: Record<string, any>) {
  router.push({ path: '/data', query: { tab: 'emails', contact_id: row.id } })
}

async function archiveSelected() {
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${selectedRows.value.length} 位联系人吗？其关联邮箱也将移至归档。`, '批量删除联系人', { type: 'warning' })
    archiving.value = true
    const { data } = await api.post('/contacts/bulk-archive', { ids: selectedRows.value.map((row) => row.id) })
    selectedRows.value = []
    ElMessage.success(`已归档 ${data.contacts_archived} 位联系人及 ${data.emails_archived} 条关联邮箱`)
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
    const response = await api.post('/contacts/export', { ids: selectedRows.value.map((row) => row.id) }, { responseType: 'blob' })
    if (lastExport.value) URL.revokeObjectURL(lastExport.value.url)
    const filename = response.headers['content-disposition']?.match(/filename="?([^";]+)"?/)?.[1] || 'buyerreach-contacts-selected.csv'
    lastExport.value = {
      count: Number(response.headers['x-exported-count'] || selectedRows.value.length),
      filename,
      url: URL.createObjectURL(response.data),
    }
    downloadLastExport()
    ElMessage.success(`已导出 ${lastExport.value.count} 位联系人及其关联邮箱`)
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
</script>

<style scoped>
.export-status { display: flex; align-items: center; gap: 10px; margin: 0 0 16px; color: var(--el-text-color-regular); }
</style>
