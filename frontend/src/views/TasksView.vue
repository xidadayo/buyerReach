<template>
  <div class="page-heading">
    <h1 class="page-title">搜索任务</h1>
    <div>
      <el-button @click="aiDialogVisible = true">AI 协调任务</el-button>
      <el-button v-if="batchExactBrandEnabled" @click="$router.push('/batch-exact-brand')">批量精准品牌</el-button>
      <el-button type="primary" @click="dialogVisible = true">创建任务</el-button>
    </div>
  </div>

  <div class="panel">
    <EntityTable ref="table" endpoint="/search-tasks" :columns="columns">
      <template #cell-mode="{ value }">
        {{ modeLabel(value) }}
      </template>
      <template #cell-status="{ row }">
        <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
      </template>
      <template #cell-progress="{ row, value }">
        <template v-if="row.mode === 'batch_exact_brand'">
          <span>目标 {{ value?.targets_total || 0 }} · 已完成 {{ value?.targets_completed || 0 }}</span>
          <el-tag v-if="value?.targets_failed > 0" type="danger" size="small" style="margin-left: 8px">失败 {{ value.targets_failed }}</el-tag>
        </template>
        <template v-else>
          <span>品牌 {{ value?.brands || 0 }} · 联系人 {{ value?.contacts || 0 }} · 邮箱 {{ value?.emails || 0 }}</span>
          <el-tag v-if="value?.partial_failure_count" type="warning" size="small" style="margin-left: 8px">部分查询失败 {{ value.partial_failure_count }}</el-tag>
        </template>
      </template>
      <template #actions="{ row }">
        <el-button
          v-if="canStart(row.status)"
          size="small"
          type="primary"
          @click="canRerun(row.status) ? rerun(row.id) : start(row.id)"
        >
          启动
        </el-button>
        <el-button v-if="row.status === 'queued' || row.status === 'running'" size="small" type="warning" @click="pause(row.id)">暂停</el-button>
        <el-button v-if="row.status === 'paused'" size="small" type="success" @click="resume(row.id)">继续</el-button>
        <el-button v-if="canCancel(row.status)" size="small" type="danger" plain @click="cancel(row.id)">取消</el-button>
        <el-button size="small" @click="copy(row.id)">复制</el-button>
        <el-button size="small" @click="showDetail(row)">详情</el-button>
      </template>
    </EntityTable>
  </div>

  <el-dialog v-model="dialogVisible" title="新建搜索任务" width="620px">
    <el-form label-width="110px" :model="form">
      <el-form-item label="任务名称" required><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="任务模式">
        <el-segmented v-model="form.mode" :options="modes" @change="loadVendorCapabilities" />
      </el-form-item>
      <el-form-item v-if="form.mode === 'exact_brand'" label="目标品牌" required>
        <el-input v-model="keywords" type="textarea" :rows="3" placeholder="填写一个已知目标品牌，例如 MANGO" />
      </el-form-item>
      <el-form-item v-if="form.mode === 'exact_brand'" label="官方官网/域名" required>
        <el-input v-model="officialDomains" placeholder="例如 mango.com；用于排除同名公司" />
      </el-form-item>
      <el-alert v-if="form.mode === 'exact_brand'" title="精准品牌会同时校验品牌名和官网域名；域名不匹配时不会创建品牌、联系人或邮箱。" type="info" :closable="false" show-icon style="margin-bottom: 16px" />
      <el-form-item label="品牌归属国家" :required="form.mode === 'brand_discovery'"><el-input v-model="countries" placeholder="填写品牌总部、注册地或起源国家；不是销售市场。多个国家用逗号分隔" /></el-form-item>
      <el-alert v-if="form.mode === 'brand_discovery'" title="品牌发现只按“品牌归属国家 + 目标品类”搜索未知品牌；仅在当地销售或运营的外国品牌不收录。" type="warning" :closable="false" show-icon style="margin-bottom: 16px" />
      <el-form-item v-if="form.mode === 'brand_discovery'" label="目标品类（必填）" required><el-input v-model="categories" placeholder="例如 fast fashion, luggage；复合目标请配合‘全部匹配’" /></el-form-item>
      <el-form-item v-if="form.mode === 'brand_discovery'" label="品类匹配方式">
        <el-radio-group v-model="form.category_match_mode">
          <el-radio-button value="all">全部匹配（复合关键词）</el-radio-button>
          <el-radio-button value="any">任一匹配（多个独立品类）</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.mode === 'brand_discovery'" label="最低相关性"><el-input-number v-model="form.min_relevance" :min="0" :max="100" /></el-form-item>
      <el-form-item label="数据来源">
        <el-radio-group v-model="vendorExecutionMode">
          <el-radio-button value="apollo_only" :disabled="!vendorAvailable('apollo')">仅 Apollo</el-radio-button>
          <el-radio-button value="hunter_only" :disabled="!vendorAvailable('hunter')">仅 Hunter</el-radio-button>
          <el-radio-button value="apollo_hunter" :disabled="!vendorAvailable('apollo') || !vendorAvailable('hunter')">Apollo + Hunter</el-radio-button>
        </el-radio-group>
        <el-text size="small" type="info" style="margin-top: 4px">
          选中的来源会独立执行公司、联系人和邮箱流程；双来源会增加调用量和费用。
        </el-text>
      </el-form-item>
      <el-form-item label="目标职位">
        <el-input v-model="titles" placeholder="多个职位用逗号分隔" />
      </el-form-item>
      <el-form-item label="联系人上限">
        <el-input-number v-model="contactsLimitPerBrand" :min="1" :max="50" />
      </el-form-item>
      <el-form-item v-if="form.mode === 'brand_discovery'" label="品牌上限">
        <el-input-number v-model="form.brand_limit" :min="1" :max="5000" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="createTask">创建</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="aiDialogVisible" title="AI 协调任务" width="820px" top="4vh">
    <el-alert title="描述目标后，AI 会生成搜索、官网解析和联系人补充方案；确认后才会创建任务。" type="info" :closable="false" show-icon style="margin-bottom: 16px" />
    <el-input v-model="aiPrompt" type="textarea" :rows="4" placeholder="例如：寻找美国女包品牌" />
    <div v-if="aiPlan" style="margin-top: 18px; max-height: 62vh; overflow-y: auto; padding-right: 4px">
      <el-tag :type="aiPlan.source === 'ai' ? 'success' : 'warning'">{{ aiPlan.source === 'ai' ? 'AI 方案' : '本地规划规则' }}</el-tag>
      <el-alert v-if="aiPlan.fallback_reason" :title="`AI 已调用但解析失败：${aiPlan.fallback_reason}`" type="error" :closable="false" show-icon style="margin-top: 12px" />
      <el-alert v-if="aiPlan.requires_confirmation" title="当前解释可能实质影响结果，请确认下方业务范围后再开始搜索。" type="warning" :closable="false" show-icon style="margin-top: 12px" />
      <el-descriptions title="系统理解的搜索意图" :column="2" border style="margin-top: 12px">
        <el-descriptions-item label="国家">{{ intentQualifiers('country').join('、') || '未指定' }}</el-descriptions-item>
        <el-descriptions-item label="业务语境">{{ intentQualifiers('industry_context').join('、') || '待确认' }}</el-descriptions-item>
        <el-descriptions-item label="目标概念" :span="2">{{ (aiPlan.search_intent?.target_concepts || []).map((item: any) => item.normalized_label).join('、') }}</el-descriptions-item>
        <el-descriptions-item label="商家类型">{{ (aiPlan.search_intent?.business_types || []).join('、') || '未限定' }}</el-descriptions-item>
        <el-descriptions-item label="解析来源">{{ aiPlan.search_intent?.source === 'ai' ? 'AI 语境解析' : '本地兼容解析' }}</el-descriptions-item>
      </el-descriptions>
      <el-collapse style="margin-top: 12px">
        <el-collapse-item title="高级范围与知识来源" name="intent-details">
          <pre>{{ JSON.stringify(aiPlan.search_intent, null, 2) }}</pre>
        </el-collapse-item>
      </el-collapse>
      <el-text type="info" style="margin-left: 10px">以下参数可在创建任务前编辑</el-text>
      <el-descriptions :column="2" border style="margin-top: 12px">
        <el-descriptions-item label="任务名称">
          <el-input v-model="aiDraft.name" maxlength="255" />
        </el-descriptions-item>
        <el-descriptions-item label="模式">{{ aiPlan.task.mode === 'brand_discovery' ? '品牌发现' : '精确品牌' }}</el-descriptions-item>
        <el-descriptions-item v-if="aiPlan.task.mode === 'exact_brand'" label="目标品牌">
          <el-input v-model="aiDraft.brandKeywords" placeholder="多个品牌用逗号分隔" />
        </el-descriptions-item>
        <el-descriptions-item v-if="aiPlan.task.mode === 'exact_brand'" label="官方域名">
          <el-input v-model="aiDraft.officialDomains" placeholder="例如 mango.com" />
        </el-descriptions-item>
        <el-descriptions-item label="品牌归属国家">
          <el-input v-model="aiDraft.countries" placeholder="多个国家用逗号分隔" />
        </el-descriptions-item>
        <el-descriptions-item label="目标品类">
          <el-input v-model="aiDraft.categories" placeholder="多个品类用逗号分隔" />
        </el-descriptions-item>
        <el-descriptions-item v-if="aiPlan.task.mode === 'brand_discovery'" label="品牌数量上限">
          <el-input-number v-model="aiDraft.brandLimit" :min="1" :max="5000" controls-position="right" />
        </el-descriptions-item>
        <el-descriptions-item label="数据来源" :span="2">
          <el-radio-group v-model="vendorExecutionMode">
            <el-radio-button value="apollo_only" :disabled="!vendorAvailable('apollo')">仅 Apollo</el-radio-button>
            <el-radio-button value="hunter_only" :disabled="!vendorAvailable('hunter')">仅 Hunter</el-radio-button>
            <el-radio-button value="apollo_hunter" :disabled="!vendorAvailable('apollo') || !vendorAvailable('hunter')">Apollo + Hunter</el-radio-button>
          </el-radio-group>
        </el-descriptions-item>
        <el-descriptions-item label="每品牌联系人上限">
          <el-input-number v-model="aiDraft.contactsLimitPerBrand" :min="1" :max="50" controls-position="right" />
        </el-descriptions-item>
        <el-descriptions-item v-if="aiPlan.task.mode === 'brand_discovery'" label="最低相关度">
          <el-input-number v-model="aiDraft.minRelevance" :min="0" :max="100" controls-position="right" />
        </el-descriptions-item>
        <el-descriptions-item label="目标职位" :span="2">
          <el-input v-model="aiDraft.targetTitles" type="textarea" :rows="2" placeholder="多个职位用逗号分隔" />
        </el-descriptions-item>
      </el-descriptions>
      <el-text type="info" size="small">
        数量上限控制最终保留的品牌数，以及每个合格品牌最多补充的联系人数量。
      </el-text>
      <div style="margin-top: 14px"><strong>执行步骤</strong><ol><li v-for="step in aiPlan.steps" :key="step">{{ step }}</li></ol></div>
      <el-alert v-for="warning in aiPlan.warnings" :key="warning" :title="warning" type="warning" :closable="false" show-icon style="margin-top: 8px" />
    </div>
    <template #footer>
      <el-button @click="aiDialogVisible = false">取消</el-button>
      <el-button :loading="aiPlanning" @click="generateAiPlan">生成方案</el-button>
      <el-button type="primary" :disabled="!aiPlan" :loading="saving" @click="confirmAiPlan">确认创建任务</el-button>
    </template>
  </el-dialog>

  <!-- Task detail drawer -->
  <el-drawer v-model="detailVisible" title="任务详情" size="580px">
    <el-descriptions v-if="selected" :column="1" border>
      <el-descriptions-item label="任务 ID">{{ selected.id }}</el-descriptions-item>
      <el-descriptions-item label="模式">{{ modeLabel(selected.mode) }}</el-descriptions-item>
      <el-descriptions-item label="状态">{{ statusLabel(selected.status) }}</el-descriptions-item>
      <el-descriptions-item label="错误信息">{{ selected.error_message || '-' }}</el-descriptions-item>
      <el-descriptions-item label="筛选条件"><pre>{{ JSON.stringify(selected.filters, null, 2) }}</pre></el-descriptions-item>
    </el-descriptions>
    <template v-if="taskPlan">
      <el-divider>Vendor 执行计划</el-divider>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="执行模式">{{ executionModeLabel(taskPlan.execution_mode) }}</el-descriptions-item>
        <el-descriptions-item label="选中来源">{{ taskPlan.selected_vendors?.join(' + ') || '无' }}</el-descriptions-item>
        <el-descriptions-item label="Adapter 版本">{{ taskPlan.adapter_version }}</el-descriptions-item>
      </el-descriptions>
    </template>

    <!-- Batch target details -->
    <template v-if="selected?.mode === 'batch_exact_brand'">
      <el-divider>批量目标进度</el-divider>
      <el-table :data="batchTargets" size="small" max-height="320" v-loading="batchTargetsLoading">
        <el-table-column prop="row_number" label="#" width="50" />
        <el-table-column prop="company_name" label="公司" min-width="140" show-overflow-tooltip />
        <el-table-column prop="normalized_domain" label="域名" min-width="150" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="batchStatusType(row.execution_status)" size="small">
              {{ batchStatusLabel(row.execution_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reliable_email_count" label="可靠邮箱" width="80" />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button
              v-if="['failed','retryable'].includes(row.execution_status)"
              size="small" type="warning" @click="retrySingleTarget(row.id)"
            >重试</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <template v-if="selected?.mode !== 'batch_exact_brand'">
      <el-divider>阶段执行与恢复点</el-divider>
      <el-table :data="taskCheckpoints" size="small" max-height="300">
        <el-table-column prop="stage" label="阶段" min-width="130" />
        <el-table-column prop="vendor" label="平台" width="95" />
        <el-table-column prop="status" label="状态" width="90"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
        <el-table-column prop="attempts" label="次数" width="65" />
        <el-table-column label="结果" width="80"><template #default="{ row }">{{ checkpointCount(row) }}</template></el-table-column>
        <el-table-column prop="error_message" label="切换/失败原因" min-width="190" show-overflow-tooltip />
      </el-table>
      <el-divider>任务明细</el-divider>
      <el-table :data="taskItems" size="small" max-height="360">
        <el-table-column prop="entity_type" label="对象" width="90" />
        <el-table-column prop="stage" label="阶段" min-width="140" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="provider" label="Provider" min-width="130" />
        <el-table-column prop="error_message" label="错误" min-width="180" show-overflow-tooltip />
      </el-table>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import EntityTable, { type TableColumn } from '../components/EntityTable.vue'
import { api } from '../api/client'

const table = ref<InstanceType<typeof EntityTable>>()
const dialogVisible = ref(false)
const aiDialogVisible = ref(false)
const detailVisible = ref(false)
const selected = ref<any>(null)
const taskItems = ref<any[]>([])
const taskCheckpoints = ref<any[]>([])
const taskPlan = ref<any>(null)
const saving = ref(false)
const aiPlanning = ref(false)
const aiPrompt = ref('')
const aiPlan = ref<any>(null)
const aiDraft = reactive({
  name: '',
  brandKeywords: '',
  officialDomains: '',
  countries: '',
  categories: '',
  targetTitles: '',
  brandLimit: 100,
  contactsLimitPerBrand: 5,
  minRelevance: 45,
})
const keywords = ref('')
const officialDomains = ref('')
const countries = ref('')
const categories = ref('')
const vendorExecutionMode = ref<'apollo_only' | 'hunter_only' | 'apollo_hunter'>('apollo_hunter')
const vendorCapabilities = ref<Record<string, any>>({})
const batchExactBrandEnabled = ref(false)
const contactsLimitPerBrand = ref(5)
const titles = ref('Head of Buying,Sourcing Manager')
const form = reactive({ name: '', mode: 'brand_discovery', brand_limit: 100, min_relevance: 45, category_match_mode: 'any' })
const modes = [
  { label: '品牌发现', value: 'brand_discovery' },
  { label: '精确品牌', value: 'exact_brand' },
]
const columns: TableColumn[] = [
  { key: 'name', label: '任务名称', width: 180 },
  { key: 'mode', label: '模式', width: 120 },
  { key: 'status', label: '状态', width: 110 },
  { key: 'progress', label: '处理结果', width: 260 },
  { key: 'error_message', label: '错误信息', width: 260 },
  { key: 'created_at', label: '创建时间', width: 180 },
]

function selectedVendors() {
  if (vendorExecutionMode.value === 'apollo_only') return ['apollo']
  if (vendorExecutionMode.value === 'hunter_only') return ['hunter']
  return ['apollo', 'hunter']
}

function vendorAvailable(vendor: string) {
  return vendorCapabilities.value[vendor]?.available !== false
}

async function loadVendorCapabilities() {
  try {
    const { data } = await api.get('/vendor-capabilities', { params: { task_mode: form.mode } })
    vendorCapabilities.value = Object.fromEntries(
      (Array.isArray(data) ? data : []).map((item: any) => [item.vendor, item]),
    )
  } catch {
    vendorCapabilities.value = {}
  }
}

async function loadBatchExactBrandCapability() {
  try {
    const { data } = await api.get('/batch-exact-brand/capabilities')
    batchExactBrandEnabled.value = data?.enabled === true
  } catch {
    batchExactBrandEnabled.value = false
  }
}

async function createTask() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写任务名称')
    return
  }
  if (form.mode === 'exact_brand' && !keywords.value.trim()) {
    ElMessage.warning('精准品牌模式需要填写目标品牌')
    return
  }
  if (form.mode === 'brand_discovery' && !splitComma(categories.value).length) {
    ElMessage.warning('品牌发现需要填写目标品类，以过滤无关品牌')
    return
  }
  if (form.mode === 'brand_discovery' && !splitComma(countries.value).length) {
    ElMessage.warning('品牌发现需要填写品牌归属国家')
    return
  }
  if (form.mode === 'exact_brand' && !splitComma(officialDomains.value).length) {
    ElMessage.warning('精准品牌必须填写已确认的官方官网或域名，以排除同名公司')
    return
  }
  if (!splitComma(titles.value).length) {
    ElMessage.warning('请至少填写一个目标职位')
    return
  }
  saving.value = true
  try {
    const exactBrandKeywords = form.mode === 'exact_brand' ? splitLines(keywords.value) : []
    await api.post('/search-tasks', {
      ...form,
      brand_keywords: exactBrandKeywords,
      brand_limit: form.mode === 'exact_brand' ? exactBrandKeywords.length : form.brand_limit,
      official_domains: splitComma(officialDomains.value),
      countries: splitComma(countries.value),
      categories: splitComma(categories.value),
      category_match_mode: form.category_match_mode,
      target_titles: splitComma(titles.value),
      contacts_limit_per_brand: contactsLimitPerBrand.value,
      selected_vendors: selectedVendors(),
    })
    dialogVisible.value = false
    form.name = ''
    keywords.value = ''
    officialDomains.value = ''
    countries.value = ''
    categories.value = ''
    ElMessage.success('任务已创建')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    saving.value = false
  }
}

async function generateAiPlan() {
  if (aiPrompt.value.trim().length < 4) return ElMessage.warning('请至少输入 4 个字符的目标描述')
  aiPlanning.value = true
  try {
    const { data } = await api.post(
      '/ai/task-plans',
      { prompt: aiPrompt.value.trim() },
      { timeout: 195_000 },
    )
    aiPlan.value = data
    loadAiDraft(data.task)
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    aiPlanning.value = false
  }
}

async function confirmAiPlan() {
  if (!aiPlan.value) return
  const task = buildAiTaskPayload()
  if (!task.name) {
    ElMessage.warning('请填写任务名称')
    return
  }
  if (task.mode === 'brand_discovery' && !task.countries.length) {
    ElMessage.warning('品牌发现任务必须填写品牌归属国家')
    return
  }
  if (task.mode === 'brand_discovery' && !task.categories.length) {
    ElMessage.warning('品牌发现任务必须填写目标品类')
    return
  }
  if (task.mode === 'exact_brand' && !task.brand_keywords.length) {
    ElMessage.warning('精准品牌任务必须填写目标品牌')
    return
  }
  if (task.mode === 'exact_brand' && !task.official_domains.length) {
    ElMessage.warning('精准品牌任务必须填写已确认的官方网站或域名')
    return
  }
  if (!task.target_titles.length) {
    ElMessage.warning('请至少填写一个目标职位')
    return
  }
  saving.value = true
  try {
    await api.post('/search-tasks', task)
    aiDialogVisible.value = false
    aiPlan.value = null
    aiPrompt.value = ''
    ElMessage.success('AI 方案已创建为待启动任务，请确认后在列表中启动')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    saving.value = false
  }
}

function loadAiDraft(task: any) {
  aiDraft.name = String(task?.name || '')
  aiDraft.brandKeywords = (task?.brand_keywords || []).join(', ')
  aiDraft.officialDomains = (task?.official_domains || []).join(', ')
  aiDraft.countries = (task?.countries || []).join(', ')
  aiDraft.categories = (task?.categories || []).join(', ')
  aiDraft.targetTitles = (task?.target_titles?.length
    ? task.target_titles
    : ['Buyer', 'Head of Buying', 'Sourcing Manager', 'Procurement Manager']).join(', ')
  aiDraft.brandLimit = Number(task?.brand_limit || 100)
  aiDraft.contactsLimitPerBrand = Number(task?.contacts_limit_per_brand || 5)
  aiDraft.minRelevance = Number(task?.min_relevance ?? 45)
}

function buildAiTaskPayload() {
  const exactBrandKeywords = aiPlan.value.task.mode === 'exact_brand'
    ? splitComma(aiDraft.brandKeywords)
    : []
  return {
    ...aiPlan.value.task,
    original_prompt: aiPrompt.value.trim(),
    search_intent: aiPlan.value.search_intent,
    name: aiDraft.name.trim(),
    brand_keywords: exactBrandKeywords,
    official_domains: aiPlan.value.task.mode === 'exact_brand' ? splitComma(aiDraft.officialDomains) : [],
    countries: splitComma(aiDraft.countries),
    categories: splitComma(aiDraft.categories),
    target_titles: splitComma(aiDraft.targetTitles),
    brand_limit: aiPlan.value.task.mode === 'exact_brand'
      ? Math.max(exactBrandKeywords.length, 1)
      : aiDraft.brandLimit,
    contacts_limit_per_brand: aiDraft.contactsLimitPerBrand,
    selected_vendors: selectedVendors(),
    min_relevance: aiDraft.minRelevance,
  }
}

function intentQualifiers(type: string) {
  return (aiPlan.value?.search_intent?.global_qualifiers || [])
    .filter((item: any) => item.type === type)
    .map((item: any) => item.value)
}

async function start(id: string) {
  try {
    await api.post(`/search-tasks/${id}/start`)
    ElMessage.success('任务已进入队列')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function rerun(id: string) {
  try {
    const response = await api.post(`/search-tasks/${id}/copy`)
    await api.post(`/search-tasks/${response.data.id}/start`)
    ElMessage.success('已复制为新任务并启动')
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function showDetail(row: any) {
  const [taskResponse, itemsResponse, checkpointsResponse] = await Promise.all([
    api.get(`/search-tasks/${row.id}`),
    api.get(`/search-tasks/${row.id}/items`, { params: { page_size: 100 } }),
    api.get(`/search-tasks/${row.id}/checkpoints`),
  ])
  selected.value = taskResponse.data
  taskItems.value = itemsResponse.data.items || []
  taskPlan.value = checkpointsResponse.data.plan
  taskCheckpoints.value = checkpointsResponse.data.checkpoints || []
  detailVisible.value = true

  // Load batch targets for batch tasks
  if (taskResponse.data.mode === 'batch_exact_brand') {
    loadBatchTargets(row.id)
  }
}

function checkpointCount(row: any) {
  const output = row.normalized_output || {}
  const list = Object.values(output).find((value) => Array.isArray(value))
  return Array.isArray(list) ? list.length : row.status === 'completed' ? 1 : 0
}

async function pause(id: string) {
  await taskAction(id, 'pause', '任务已暂停')
}

async function resume(id: string) {
  await taskAction(id, 'resume', '任务已重新入队')
}

async function cancel(id: string) {
  try {
    await ElMessageBox.confirm('取消后不能直接恢复，是否继续？', '确认取消', { type: 'warning' })
    await taskAction(id, 'cancel', '任务已取消')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message)
  }
}

async function copy(id: string) {
  await taskAction(id, 'copy', '任务已复制为草稿')
}

async function taskAction(id: string, action: string, message: string) {
  try {
    await api.post(`/search-tasks/${id}/${action}`)
    ElMessage.success(message)
    await table.value?.load()
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

function splitLines(value: string) {
  return value.split(/[\r\n,，、;；]+/).map((item) => item.trim()).filter(Boolean)
}
function splitComma(value: string) {
  return value.split(/[\r\n,，、;；]+/).map((item) => item.trim()).filter(Boolean)
}
function modeLabel(value: string) {
  return ({ brand_discovery: '品牌发现', exact_brand: '精准品牌', excel_import: 'Excel 导入', batch_exact_brand: '批量精准品牌' } as Record<string, string>)[value] || value
}
function statusLabel(value: string) {
  return ({ draft: '草稿', queued: '排队中', running: '执行中', paused: '已暂停', completed: '已完成', failed: '失败', partial: '部分完成', cancelled: '已取消' } as Record<string, string>)[value] || value
}
function executionModeLabel(value: string) {
  return ({ apollo_only: '仅 Apollo', hunter_only: '仅 Hunter', apollo_hunter: 'Apollo + Hunter' } as Record<string, string>)[value] || value || '未知'
}
function statusType(value: string) {
  if (value === 'completed') return 'success'
  if (value === 'failed') return 'danger'
  if (value === 'cancelled') return 'info'
  if (value === 'running' || value === 'queued') return 'warning'
  return 'info'
}
function canCancel(value: string) {
  return ['draft', 'queued', 'running', 'paused', 'failed'].includes(value)
}
function canStart(value: string) {
  return ['draft', 'failed', 'completed', 'cancelled'].includes(value)
}
function canRerun(value: string) {
  return ['completed', 'cancelled'].includes(value)
}

// ── Batch target helpers ──────────────────────────────────────────────────
const batchTargets = ref<any[]>([])
const batchTargetsLoading = ref(false)

async function loadBatchTargets(taskId: string) {
  batchTargetsLoading.value = true
  try {
    const { data } = await api.get(`/search-tasks/${taskId}/targets`, { params: { page_size: 200 } })
    batchTargets.value = data.items || []
  } catch {
    batchTargets.value = []
  } finally {
    batchTargetsLoading.value = false
  }
}

function batchStatusLabel(status: string) {
  const map: Record<string, string> = {
    pending: '等待中', queued: '排队中', running: '执行中', completed: '已完成',
    no_match: '未找到', partial: '部分完成', retryable: '可重试', failed: '失败', cancelled: '已取消',
  }
  return map[status] || status
}

function batchStatusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'running') return 'warning'
  if (['failed', 'retryable'].includes(status)) return 'danger'
  return ''
}

async function retrySingleTarget(targetId: string) {
  if (!selected.value) return
  try {
    await api.post(`/search-tasks/${selected.value.id}/targets/retry`, { target_ids: [targetId] })
    ElMessage.success('已重新排队')
    await loadBatchTargets(selected.value.id)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || error.message || '重试失败')
  }
}

let timer: number | undefined
onMounted(() => {
  loadVendorCapabilities()
  loadBatchExactBrandCapability()
  timer = window.setInterval(() => table.value?.load(), 5000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>
