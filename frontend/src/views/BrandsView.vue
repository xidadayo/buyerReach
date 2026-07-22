<template>
  <div class="page-heading">
    <h1 class="page-title">品牌库</h1>
    <el-space>
      <el-button type="danger" :disabled="!selectedRows.length" :loading="archiving" @click="archiveSelected">批量删除 {{ selectedRows.length ? `(${selectedRows.length})` : '' }}</el-button>
      <el-button type="primary" @click="dialogVisible = true">新增品牌</el-button>
    </el-space>
  </div>
  <div class="panel">
    <EntityTable ref="table" endpoint="/brands" :columns="columns" selectable @selection-change="onSelectionChange">
      <template #cell-primary_website="{ value }">
        <a v-if="value" :href="value" target="_blank" rel="noreferrer">{{ value }}</a>
        <span v-else>-</span>
      </template>
      <template #cell-email_count="{ value, row }">
        <router-link
          v-if="Number(value) > 0"
          class="email-count-link"
          :to="{ path: '/emails', query: { brand_id: row.id, brand_name: row.name } }"
          :aria-label="`查看 ${row.name} 的 ${value} 个关联邮箱`"
        >
          {{ value }}
        </router-link>
        <span v-else>0</span>
      </template>
      <template #actions="{ row }">
        <el-button v-if="row.status === 'pending_review'" size="small" type="success" :loading="approvingId === row.id" @click="approveDiscovery(row)">批准并丰富</el-button>
        <el-button size="small" @click="openEdit(row)">编辑</el-button>
        <el-button
          v-if="row.primary_website"
          size="small"
          :loading="parsingId === row.id"
          @click="parseWebsite(row)"
        >
          解析官网
        </el-button>
        <el-button size="small" type="danger" plain @click="archive(row)">归档</el-button>
      </template>
    </EntityTable>
  </div>

  <el-dialog v-model="dialogVisible" :title="editingId ? '编辑品牌' : '新增品牌'" width="560px">
    <el-form label-width="100px" :model="form">
      <el-form-item label="品牌名称" required><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="公司名称"><el-input v-model="form.company_name" /></el-form-item>
      <el-form-item label="官方网站"><el-input v-model="form.website" placeholder="https://" /></el-form-item>
      <el-form-item label="国家"><el-input v-model="form.country" /></el-form-item>
      <el-form-item label="品类"><el-input v-model="form.category" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>

  <!-- Website Parse Result -->
  <el-dialog v-model="parseVisible" title="官网解析结果" width="600px">
    <template v-if="parseResult">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="网站标题">{{ parseResult.title || '-' }}</el-descriptions-item>
        <el-descriptions-item label="域名">{{ parseResult.domain }}</el-descriptions-item>
        <el-descriptions-item label="邮箱数">{{ parseResult.emails?.length || 0 }}</el-descriptions-item>
        <el-descriptions-item label="电话数">{{ parseResult.phones?.length || 0 }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="parseResult.emails?.length" style="margin-top: 16px">
        <h4>发现邮箱</h4>
        <el-table :data="parseResult.emails" border size="small">
          <el-table-column prop="address" label="邮箱" min-width="200" />
          <el-table-column prop="type" label="类型" width="90" />
          <el-table-column prop="confidence" label="可信度" width="90" />
        </el-table>
      </div>
      <div v-if="parseResult.phones?.length" style="margin-top: 12px">
        <el-tag v-for="phone in parseResult.phones" :key="phone" style="margin: 4px">{{ phone }}</el-tag>
      </div>
      <div v-if="parseResult.social_links && Object.keys(parseResult.social_links).length" style="margin-top: 12px">
        <h4>社交媒体</h4>
        <div v-for="(link, platform) in parseResult.social_links" :key="platform">
          <a :href="link" target="_blank" rel="noreferrer">{{ platform }}</a>
        </div>
      </div>
      <el-alert v-if="parseResult.error" :title="parseResult.error" type="warning" show-icon style="margin-top: 12px" />
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { reactive, ref } from 'vue'
import EntityTable, { type TableColumn } from '../components/EntityTable.vue'
import { api } from '../api/client'

const table = ref<InstanceType<typeof EntityTable>>()
const dialogVisible = ref(false)
const saving = ref(false)
const archiving = ref(false)
const editingId = ref('')
const parsingId = ref('')
const approvingId = ref('')
const parseResult = ref<any>(null)
const parseVisible = ref(false)
const selectedRows = ref<Record<string, any>[]>([])
const form = reactive({ name: '', company_name: '', website: '', country: '', category: '' })
const columns: TableColumn[] = [
  { key: 'name', label: '品牌名称', width: 180 },
  { key: 'company_name', label: '公司', width: 180 },
  { key: 'primary_website', label: '官网', width: 220 },
  { key: 'country', label: '国家', width: 100 },
  { key: 'category', label: '品类', width: 130 },
  { key: 'discovery_score', label: '相关性', width: 90 },
  { key: 'email_count', label: '关联邮箱数', width: 110 },
  { key: 'created_at', label: '创建时间', width: 180 },
]

async function save() {
  if (!form.name.trim()) return ElMessage.warning('请填写品牌名称')
  saving.value = true
  try {
    const payload = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value || null]))
    if (editingId.value) await api.patch(`/brands/${editingId.value}`, payload)
    else await api.post('/brands', payload)
    Object.assign(form, { name: '', company_name: '', website: '', country: '', category: '' })
    editingId.value = ''
    dialogVisible.value = false
    ElMessage.success('品牌已保存')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    saving.value = false
  }
}

function openEdit(row: any) {
  editingId.value = row.id
  Object.assign(form, { name: row.name || '', company_name: row.company_name || '', website: row.primary_website || '', country: row.country || '', category: row.category || '' })
  dialogVisible.value = true
}

async function archive(row: any) {
  try {
    await ElMessageBox.confirm(`确认归档品牌“${row.name}”？其专属联系人、邮箱和官网也会同步归档。`, '归档确认', { type: 'warning' })
    const { data } = await api.post('/brands/bulk-archive', { ids: [row.id] })
    ElMessage.success(`已归档 ${data.archived} 个品牌、${data.contacts_archived} 位联系人、${data.emails_archived} 个邮箱`)
    await table.value?.load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message)
  }
}

function onSelectionChange(rows: Record<string, any>[]) {
  selectedRows.value = rows
}

async function archiveSelected() {
  try {
    await ElMessageBox.confirm(`确认归档选中的 ${selectedRows.value.length} 个品牌吗？其专属联系人、邮箱和官网也会同步归档。`, '批量删除品牌', { type: 'warning' })
    archiving.value = true
    const { data } = await api.post('/brands/bulk-archive', { ids: selectedRows.value.map((row) => row.id) })
    selectedRows.value = []
    ElMessage.success(`已归档 ${data.archived} 个品牌、${data.contacts_archived} 位联系人、${data.emails_archived} 个邮箱`)
    await table.value?.load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message)
  } finally {
    archiving.value = false
  }
}

async function approveDiscovery(row: any) {
  approvingId.value = row.id
  try {
    const { data } = await api.post(`/brands/${row.id}/approve-discovery`)
    ElMessage.success(`已批准候选品牌，并创建丰富任务 ${data.task_id}`)
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    approvingId.value = ''
  }
}

async function parseWebsite(row: any) {
  parsingId.value = row.id
  try {
    const { data } = await api.post(`/brands/${row.id}/parse-website`)
    parseResult.value = data
    parseVisible.value = true
    if (data.emails?.length) {
      ElMessage.success(`从官网解析到 ${data.emails.length} 个邮箱`)
    } else if (data.error) {
      ElMessage.warning(data.error)
    } else {
      ElMessage.info('未从官网解析到邮箱')
    }
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    parsingId.value = ''
  }
}
</script>

<style scoped>
.email-count-link {
  color: var(--el-color-primary);
  text-decoration: none;
}

.email-count-link:hover,
.email-count-link:focus-visible {
  text-decoration: underline;
}
</style>
