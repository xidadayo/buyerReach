<template>
  <div class="table-toolbar">
    <span class="muted">共 {{ total }} 条</span>
    <el-button :loading="loading" @click="load">刷新</el-button>
  </div>
  <el-table
    v-loading="loading"
    :data="items"
    :max-height="tableMaxHeight"
    border
    :scrollbar-always-on="scrollbarAlwaysOn"
    style="width: 100%"
    @selection-change="handleSelectionChange"
  >
    <el-table-column v-if="selectable" type="selection" width="48" />
    <el-table-column
      v-for="column in columns"
      :key="column.key"
      :prop="column.key"
      :label="column.label"
      :min-width="column.width || 140"
      show-overflow-tooltip
    >
      <template #default="scope">
        <slot :name="`cell-${column.key}`" :row="scope.row" :value="scope.row[column.key]">
          {{ display(scope.row[column.key]) }}
        </slot>
      </template>
    </el-table-column>
    <el-table-column v-if="$slots.actions" label="操作" width="300" fixed="right">
      <template #default="scope"><slot name="actions" :row="scope.row" /></template>
    </el-table-column>
    <template #empty><el-empty description="暂无数据" /></template>
  </el-table>
  <el-pagination
    v-if="total > pageSize"
    v-model:current-page="page"
    :page-size="pageSize"
    :total="total"
    layout="prev, pager, next"
    style="margin-top: 16px; justify-content: flex-end"
    @current-change="load"
  />
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { api } from '../api/client'

export interface TableColumn {
  key: string
  label: string
  width?: number
}

const props = withDefaults(defineProps<{
  endpoint: string
  columns: TableColumn[]
  queryParams?: Record<string, unknown>
  pageSize?: number
  selectable?: boolean
  tableMaxHeight?: string | number
  scrollbarAlwaysOn?: boolean
}>(), {
  pageSize: 50,
  queryParams: () => ({}),
  selectable: false,
  tableMaxHeight: undefined,
  scrollbarAlwaysOn: false,
})
const emit = defineEmits<{ (event: 'selection-change', rows: Record<string, any>[]): void }>()

const items = ref<Record<string, any>[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)

async function load() {
  loading.value = true
  try {
    const { data } = await api.get(props.endpoint, {
      params: { ...props.queryParams, page: page.value, page_size: props.pageSize },
    })
    items.value = data.items || []
    total.value = data.total || 0
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

function display(value: unknown) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function handleSelectionChange(rows: Record<string, any>[]) {
  emit('selection-change', rows)
}

async function reloadFromFirstPage() {
  page.value = 1
  await load()
}

defineExpose({ load, reloadFromFirstPage })
onMounted(load)
</script>
