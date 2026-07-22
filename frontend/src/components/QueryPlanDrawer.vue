<template>
  <el-drawer
    v-model="visible"
    :title="`查询切片 — ${planLabel}`"
    size="620px"
    direction="rtl"
  >
    <template v-if="plan">
      <div style="margin-bottom: 16px; display: flex; gap: 8px; align-items: center">
        <el-tag :type="plan.status === 'locked' ? 'success' : 'warning'">
          {{ plan.status === 'locked' ? '已锁定' : plan.status === 'review' ? '审核中' : '草稿' }}
        </el-tag>
        <span style="color: var(--el-text-color-secondary); font-size: 13px">
          版本 {{ plan.version }} · {{ plan.slices?.length || 0 }} 个查询方向
        </span>
      </div>

      <!-- Summary -->
      <el-alert
        :title="summary"
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
      />

      <!-- Slices list -->
      <div v-if="(plan.slices || []).length === 0" style="text-align: center; padding: 24px; color: var(--el-text-color-secondary)">
        尚无查询切片，点击下方按钮添加
      </div>

      <div
        v-for="slice in sortedSlices"
        :key="slice.id"
        style="margin-bottom: 10px; border: 1px solid var(--el-border-color-light); border-radius: 6px; padding: 10px 12px"
        :style="{ opacity: slice.enabled ? 1 : 0.55 }"
      >
        <div style="display: flex; align-items: center; justify-content: space-between">
          <div style="flex: 1">
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px">
              <el-tag size="small" :type="purposeType(slice.purpose)">
                {{ purposeLabel(slice.purpose) }}
              </el-tag>
              <strong>{{ slice.label }}</strong>
              <el-tag v-if="!slice.enabled" size="small" type="info">已禁用</el-tag>
            </div>
            <div style="font-size: 12px; color: var(--el-text-color-secondary)">
              {{ slice.target_concepts?.join(', ') || '未指定' }}
              <template v-if="slice.countries?.length"> — {{ slice.countries.join(', ') }}</template>
              <template v-if="slice.business_types?.length"> — {{ slice.business_types.join(', ') }}</template>
            </div>
            <div v-if="slice.reason" style="font-size: 11px; color: var(--el-text-color-placeholder); margin-top: 2px">
              {{ slice.reason }}
            </div>
          </div>
          <div style="display: flex; gap: 4px; flex-shrink: 0; margin-left: 12px">
            <el-button
              v-if="!locked"
              size="small"
              :icon="slice.enabled ? 'Hide' : 'View'"
              @click="toggleSlice(slice)"
            >
              {{ slice.enabled ? '禁用' : '启用' }}
            </el-button>
            <el-button
              v-if="!locked"
              size="small"
              type="danger"
              plain
              icon="Delete"
              @click="removeSlice(slice)"
            />
          </div>
        </div>
      </div>

      <el-divider />

      <!-- Add new slice -->
      <div v-if="!locked" style="margin-top: 12px">
        <el-button type="primary" plain @click="showAddForm = !showAddForm">
          + 新增查询方向
        </el-button>
        <el-form v-if="showAddForm" style="margin-top: 12px" label-width="90px">
          <el-form-item label="名称" required>
            <el-input v-model="newSlice.label" maxlength="255" placeholder="例如：手袋 - 同义词" />
          </el-form-item>
          <el-form-item label="用途">
            <el-select v-model="newSlice.purpose">
              <el-option v-for="p in purposes" :key="p.value" :label="p.label" :value="p.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="目标概念">
            <el-input v-model="newSlice.targetConceptsText" placeholder="逗号分隔，例如：handbags, purses" />
          </el-form-item>
          <el-form-item label="国家">
            <el-input v-model="newSlice.countriesText" placeholder="ISO 代码，逗号分隔，例如：IT, FR" />
          </el-form-item>
          <el-form-item label="商家类型">
            <el-input v-model="newSlice.businessTypesText" placeholder="逗号分隔，例如：brand, retailer" />
          </el-form-item>
          <el-form-item label="包含词">
            <el-input v-model="newSlice.includeTermsText" placeholder="逗号分隔" />
          </el-form-item>
          <el-form-item label="排除词">
            <el-input v-model="newSlice.excludeTermsText" placeholder="逗号分隔" />
          </el-form-item>
          <el-form-item label="匹配方式">
            <el-radio-group v-model="newSlice.match_mode">
              <el-radio-button value="any">任意</el-radio-button>
              <el-radio-button value="all">全部</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="addingSlice" @click="addSlice">添加</el-button>
            <el-button @click="showAddForm = false">取消</el-button>
          </el-form-item>
        </el-form>
      </div>

      <!-- Lock button -->
      <div v-if="!locked" style="margin-top: 24px">
        <el-button
          type="success"
          :loading="locking"
          :disabled="!hasEnabledSlices"
          @click="lockPlan"
        >
          锁定查询计划并开始搜索
        </el-button>
        <el-text v-if="!hasEnabledSlices" type="danger" size="small" style="margin-left: 8px">
          至少需要一个启用的查询方向
        </el-text>
      </div>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'

const props = defineProps<{
  modelValue: boolean
  taskId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  locked: [plan: any]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const plan = ref<any>(null)
const locking = ref(false)
const addingSlice = ref(false)
const showAddForm = ref(false)

const newSlice = reactive({
  label: '',
  purpose: 'core' as string,
  targetConceptsText: '',
  countriesText: '',
  businessTypesText: '',
  includeTermsText: '',
  excludeTermsText: '',
  match_mode: 'any' as string,
})

const purposes = [
  { value: 'core', label: '核心' },
  { value: 'synonym', label: '同义词' },
  { value: 'local_language', label: '本地语言' },
  { value: 'business_type', label: '商家类型' },
  { value: 'adjacent', label: '相邻探索' },
  { value: 'exploratory', label: '探索' },
]

const locked = computed(() => plan.value?.status === 'locked')
const hasEnabledSlices = computed(() =>
  (plan.value?.slices || []).some((s: any) => s.enabled)
)
const sortedSlices = computed(() =>
  [...(plan.value?.slices || [])].sort((a: any, b: any) => a.priority - b.priority)
)
const planLabel = computed(() =>
  plan.value?.status === 'locked' ? `v${plan.value?.version} (锁定)` : `v${plan.value?.version}`
)
const summary = computed(() => {
  if (!plan.value) return ''
  const slices = plan.value.slices || []
  const core = slices.filter((s: any) => s.purpose === 'core').length
  const syn = slices.filter((s: any) => s.purpose === 'synonym').length
  const cnt = plan.value.target_result_count || 100
  return `目标 ${cnt} 家合格商家 · ${core} 核心查询 + ${syn} 同义词 · 共 ${slices.length} 个方向`
})

watch(
  () => props.taskId,
  async (id) => {
    if (!id) return
    await loadPlan(id)
  },
  { immediate: true },
)

async function loadPlan(taskId: string) {
  try {
    const { data } = await api.get(`/search-tasks/${taskId}/query-plans`)
    plan.value = data
  } catch {
    plan.value = null
  }
}

async function toggleSlice(slice: any) {
  if (locked.value) return
  try {
    await api.patch(
      `/search-tasks/${props.taskId}/query-plans/${plan.value.version}/slices/${slice.id}`,
      { enabled: !slice.enabled },
    )
    slice.enabled = !slice.enabled
  } catch (err: any) {
    ElMessage.error(err.message)
  }
}

async function removeSlice(slice: any) {
  if (locked.value) return
  try {
    await api.delete(
      `/search-tasks/${props.taskId}/query-plans/${plan.value.version}/slices/${slice.id}`,
    )
    plan.value.slices = (plan.value.slices || []).filter((s: any) => s.id !== slice.id)
    ElMessage.success('已删除')
  } catch (err: any) {
    ElMessage.error(err.message)
  }
}

async function addSlice() {
  if (!newSlice.label.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  addingSlice.value = true
  try {
    const { data } = await api.post(
      `/search-tasks/${props.taskId}/query-plans/${plan.value.version}/slices`,
      {
        label: newSlice.label,
        purpose: newSlice.purpose,
        target_concepts: split(newSlice.targetConceptsText),
        countries: split(newSlice.countriesText),
        business_types: split(newSlice.businessTypesText),
        include_terms: split(newSlice.includeTermsText),
        exclude_terms: split(newSlice.excludeTermsText),
        match_mode: newSlice.match_mode,
      },
    )
    plan.value.slices = [...(plan.value.slices || []), data]
    showAddForm.value = false
    Object.assign(newSlice, {
      label: '', purpose: 'core', targetConceptsText: '', countriesText: '',
      businessTypesText: '', includeTermsText: '', excludeTermsText: '', match_mode: 'any',
    })
    ElMessage.success('已添加')
  } catch (err: any) {
    ElMessage.error(err.message)
  } finally {
    addingSlice.value = false
  }
}

async function lockPlan() {
  if (!plan.value) return
  locking.value = true
  try {
    const { data } = await api.post(
      `/search-tasks/${props.taskId}/query-plans/${plan.value.version}/lock`,
      { updated_at: plan.value.updated_at },
    )
    plan.value = data
    emit('locked', data)
    ElMessage.success('查询计划已锁定')
  } catch (err: any) {
    ElMessage.error(err.message)
  } finally {
    locking.value = false
  }
}

function split(value: string) {
  return value.split(/[\r\n,，、;；]+/).map((x) => x.trim()).filter(Boolean)
}

function purposeType(value: string) {
  const map: Record<string, string> = {
    core: '', synonym: 'info', local_language: 'warning',
    business_type: 'success', adjacent: 'danger', exploratory: 'info',
  }
  return map[value] || 'info'
}

function purposeLabel(value: string) {
  const map: Record<string, string> = {
    core: '核心', synonym: '同义词', local_language: '本地语言',
    business_type: '商家类型', adjacent: '相邻', exploratory: '探索',
  }
  return map[value] || value
}

defineExpose({ loadPlan })
</script>
