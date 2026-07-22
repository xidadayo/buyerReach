<template>
  <h1 class="page-title">审核与数据工具</h1>

  <el-row :gutter="16">
    <el-col :xs="24" :lg="12">
      <div class="panel">
        <div class="panel-heading">
          <h3>导入数据</h3>
          <el-tag type="info">CSV / XLSX</el-tag>
        </div>
        <el-form label-width="90px">
          <el-form-item label="数据类型">
            <el-segmented v-model="importType" :options="entityOptions" />
          </el-form-item>
          <el-form-item label="选择文件">
            <el-upload :auto-upload="false" :limit="1" :on-change="onFileChange" :on-remove="onFileRemove">
              <el-button>选择文件</el-button>
            </el-upload>
          </el-form-item>
          <el-form-item label="字段映射">
            <el-input v-model="fieldMappingText" type="textarea" :rows="3" placeholder='可选，例如 {"name":"Brand Name","website":"Website"}' />
          </el-form-item>
          <el-form-item>
            <el-button :loading="previewing" :disabled="!file" @click="previewImport">预览</el-button>
            <el-button type="primary" :loading="importing" :disabled="!file" @click="upload">开始导入</el-button>
          </el-form-item>
        </el-form>
        <el-alert v-if="importResult" :title="importResult" type="success" :closable="false" />
        <el-table v-if="previewRows.length" :data="previewRows" size="small" max-height="240" style="margin-top: 12px">
          <el-table-column v-for="header in previewHeaders" :key="header" :prop="header" :label="header" min-width="140" show-overflow-tooltip />
        </el-table>
      </div>
    </el-col>
    <el-col :xs="24" :lg="12">
      <div class="panel">
        <h3>导出数据</h3>
        <el-form inline style="margin-bottom: 12px">
          <el-form-item label="筛选字段"><el-input v-model="exportFilterKey" placeholder="例如 status" /></el-form-item>
          <el-form-item label="筛选值"><el-input v-model="exportFilterValue" placeholder="例如 valid" /></el-form-item>
        </el-form>
        <div class="button-grid">
          <el-button v-for="item in exportOptions" :key="item.value" @click="download(item.value)">
            {{ item.label }}
          </el-button>
        </div>
      </div>
    </el-col>
  </el-row>

  <div class="panel">
    <div class="panel-heading">
      <h3>重复数据</h3>
      <el-button type="primary" :loading="checking" @click="check">执行检查</el-button>
    </div>
    <!-- Tabs: exact vs fuzzy -->
    <el-tabs v-if="checked" v-model="activeTab">
      <el-tab-pane label="精确匹配" name="exact" />
      <el-tab-pane :label="`模糊匹配 (${fuzzyGroups.length})`" name="fuzzy" v-if="fuzzyGroups.length" />
    </el-tabs>

    <!-- Exact matches -->
    <template v-if="activeTab === 'exact'">
      <el-empty v-if="checked && !groups.length" description="未发现精确重复记录" />
      <el-table v-else :data="groups" border>
        <el-table-column prop="entityLabel" label="类型" width="120" />
        <el-table-column prop="value" label="重复值" min-width="220" />
        <el-table-column prop="count" label="数量" width="90" />
        <el-table-column label="操作" width="140">
          <template #default="scope">
            <el-button size="small" type="primary" :disabled="scope.row.ids.length < 2" @click="merge(scope.row)">
              合并
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <!-- Fuzzy matches -->
    <template v-if="activeTab === 'fuzzy'">
      <el-empty v-if="checked && !fuzzyGroups.length" description="未发现模糊重复记录" />
      <el-table v-else :data="fuzzyGroups" border>
        <el-table-column prop="entityLabel" label="类型" width="100" />
        <el-table-column prop="name_a" label="记录 A" min-width="160" />
        <el-table-column prop="name_b" label="记录 B" min-width="160" />
        <el-table-column prop="similarity" label="相似度" width="100">
          <template #default="scope">
            <el-progress :percentage="scope.row.similarity" :stroke-width="8" :color="scope.row.similarity >= 90 ? '#22c55e' : '#eab308'" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="scope">
            <el-button size="small" type="primary" @click="mergeFuzzy(scope.row)">
              合并
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </div>

  <div class="panel">
    <div class="panel-heading">
      <h3>禁止发送名单</h3>
      <el-button @click="blacklistVisible = true">新增</el-button>
    </div>
    <EntityTable ref="blacklistTable" endpoint="/blacklist" :columns="blacklistColumns" />
  </div>

  <el-dialog v-model="blacklistVisible" title="新增禁止发送项" width="480px">
    <el-form label-width="90px">
      <el-form-item label="类型"><el-segmented v-model="blacklist.type" :options="blacklistTypes" /></el-form-item>
      <el-form-item label="值" required><el-input v-model="blacklist.value" /></el-form-item>
      <el-form-item label="原因"><el-input v-model="blacklist.reason" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="blacklistVisible = false">取消</el-button>
      <el-button type="primary" @click="saveBlacklist">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import { computed, reactive, ref } from 'vue'
import EntityTable, { type TableColumn } from '../components/EntityTable.vue'
import { api } from '../api/client'

const entityOptions = [{ label: '品牌', value: 'brands' }, { label: '联系人', value: 'contacts' }, { label: '邮箱', value: 'emails' }]
const exportOptions = [...entityOptions, { label: '搜索任务', value: 'tasks' }, { label: '审计日志', value: 'audit_logs' }]
const importType = ref('brands')
const file = ref<File | null>(null)
const previewing = ref(false)
const importing = ref(false)
const importResult = ref('')
const previewHeaders = ref<string[]>([])
const previewRows = ref<Record<string, string>[]>([])
const fieldMappingText = ref('')
const exportFilterKey = ref('')
const exportFilterValue = ref('')
const checking = ref(false)
const checked = ref(false)
const result = ref<any>({ exact: { duplicate_brands: [], duplicate_contacts: [], duplicate_emails: [] }, fuzzy: { brands: [], contacts: [], emails: [] } })
const blacklistVisible = ref(false)
const blacklistTable = ref<InstanceType<typeof EntityTable>>()
const blacklist = reactive({ type: 'email', value: '', reason: '' })
const blacklistTypes = [{ label: '邮箱', value: 'email' }, { label: '域名', value: 'domain' }]
const blacklistColumns: TableColumn[] = [
  { key: 'type', label: '类型', width: 100 },
  { key: 'value', label: '值', width: 240 },
  { key: 'reason', label: '原因', width: 220 },
  { key: 'created_at', label: '创建时间', width: 180 },
]

const exact = computed(() => result.value.exact || { duplicate_brands: [], duplicate_contacts: [], duplicate_emails: [] })
const fuzzy = computed(() => result.value.fuzzy || { brands: [], contacts: [], emails: [] })

const groups = computed(() => [
  ...(exact.value.duplicate_brands || []).map((item: any) => ({ ...item, entity: 'brand', entityLabel: '品牌' })),
  ...(exact.value.duplicate_contacts || []).map((item: any) => ({ ...item, entity: 'contact', entityLabel: '联系人' })),
  ...(exact.value.duplicate_emails || []).map((item: any) => ({ ...item, entity: 'email', entityLabel: '邮箱' })),
])

const fuzzyGroups = computed(() => [
  ...(fuzzy.value.brands || []).map((item: any) => ({ ...item, entity: 'brand', entityLabel: '品牌' })),
  ...(fuzzy.value.contacts || []).map((item: any) => ({ ...item, entity: 'contact', entityLabel: '联系人' })),
  ...(fuzzy.value.emails || []).map((item: any) => ({ ...item, entity: 'email', entityLabel: '邮箱' })),
])

const activeTab = ref('exact')

function onFileChange(uploadFile: UploadFile) { file.value = uploadFile.raw || null; previewHeaders.value = []; previewRows.value = [] }
function onFileRemove() { file.value = null; previewHeaders.value = []; previewRows.value = [] }

async function previewImport() {
  if (!file.value) return
  previewing.value = true
  try {
    const body = new FormData()
    body.append('file', file.value)
    const { data } = await api.post('/imports/preview', body)
    previewHeaders.value = data.headers || []
    previewRows.value = data.preview || []
    ElMessage.success(`已读取 ${data.total_rows || 0} 行，以下展示前 20 行`)
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    previewing.value = false
  }
}

async function upload() {
  if (!file.value) return
  importing.value = true
  try {
    const body = new FormData()
    body.append('entity_type', importType.value)
    body.append('file', file.value)
    if (fieldMappingText.value.trim()) JSON.parse(fieldMappingText.value)
    if (fieldMappingText.value.trim()) body.append('field_mapping', fieldMappingText.value)
    const { data } = await api.post('/imports', body)
    importResult.value = `导入完成：成功 ${data.created} 条，跳过 ${data.skipped} 条${data.errors?.length ? `，已返回 ${data.errors.length} 条错误` : ''}`
    ElMessage.success(importResult.value)
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    importing.value = false
  }
}

async function download(entityType: string) {
  try {
    const filters = exportFilterKey.value.trim() ? { [exportFilterKey.value.trim()]: exportFilterValue.value.trim() } : {}
    const response = await api.post('/exports', { entity_type: entityType, filters }, { responseType: 'blob' })
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = `buyerreach-${entityType}.csv`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function check() {
  checking.value = true
  try {
    result.value = (await api.post('/dedup/check')).data
    checked.value = true
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    checking.value = false
  }
}

async function merge(group: any) {
  await ElMessageBox.confirm(`将 ${group.count} 条”${group.value}”记录合并到第一条记录？`, '确认合并', { type: 'warning' })
  try {
    await api.post('/dedup/merge', { entity_type: group.entity, primary_id: group.ids[0], duplicate_ids: group.ids.slice(1) })
    ElMessage.success('合并完成')
    await check()
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function mergeFuzzy(group: any) {
  await ElMessageBox.confirm(
    `将 “${group.name_b}” (相似度 ${group.similarity}%) 合并到 “${group.name_a}”？`,
    '确认模糊合并',
    { type: 'warning' },
  )
  try {
    await api.post('/dedup/merge', { entity_type: group.entity, primary_id: group.id_a, duplicate_ids: [group.id_b] })
    ElMessage.success('合并完成')
    await check()
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function saveBlacklist() {
  if (!blacklist.value.trim()) return ElMessage.warning('请填写邮箱或域名')
  try {
    await api.post('/blacklist', blacklist)
    Object.assign(blacklist, { type: 'email', value: '', reason: '' })
    blacklistVisible.value = false
    ElMessage.success('已加入禁止发送名单')
    await blacklistTable.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}
</script>
