<template>
  <div class="page-heading">
    <div>
      <h1 class="page-title">工作台</h1>
      <span class="muted">数据更新于 {{ updatedAt || '-' }}</span>
    </div>
    <el-button :loading="loading" @click="load">刷新</el-button>
  </div>

  <el-row :gutter="16">
    <el-col v-for="item in metrics" :key="item.label" :xs="12" :sm="12" :md="6">
      <el-card shadow="never" class="metric-card">
        <div class="muted">{{ item.label }}</div>
        <div class="metric-value">{{ item.value }}</div>
        <div class="metric-trend">{{ item.trend }}</div>
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="16" style="margin-top: 18px">
    <el-col :span="24">
      <div class="panel">
        <h3>业务进度</h3>
        <el-steps :active="activeStep" finish-status="success" align-center>
          <el-step title="任务" />
          <el-step title="品牌官网" />
          <el-step title="联系人" />
          <el-step title="邮箱验证" />
          <el-step title="审核导出" />
        </el-steps>
      </div>
      <div class="panel">
        <h3>邮箱池状态</h3>
        <el-table :data="emailPools" style="width: 100%">
          <el-table-column prop="pool" label="邮箱池" min-width="150" />
          <el-table-column prop="count" label="数量" width="100" />
          <el-table-column prop="rule" label="状态" min-width="220" />
        </el-table>
      </div>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'

const loading = ref(false)
const updatedAt = ref('')
const data = ref<any>({ metrics: {}, email_pools: {} })

const metrics = computed(() => [
  { label: '品牌总数', value: data.value.metrics.brands || 0, trend: `${data.value.metrics.websites || 0} 个官网` },
  {
    label: '有效联系人',
    value: data.value.metrics.valid_contacts || 0,
    trend: `已发现 ${data.value.metrics.discovered_contacts ?? data.value.metrics.contacts ?? 0} 条线索`,
  },
  { label: '有效邮箱', value: data.value.metrics.valid_emails || 0, trend: '可进入导出池' },
  { label: '待审核邮箱', value: data.value.metrics.review_emails || 0, trend: '需要人工决策' },
])

const emailPools = computed(() => [
  { pool: '原始数据池', count: data.value.email_pools.raw || 0, rule: '等待验证' },
  { pool: '待验证池', count: data.value.email_pools.pending_verification || 0, rule: '验证任务处理中' },
  { pool: '人工审核池', count: data.value.email_pools.manual_review || 0, rule: '结果未知或风险邮箱' },
  { pool: '有效邮箱池', count: data.value.email_pools.valid || 0, rule: '验证或人工审核通过' },
  { pool: '无效邮箱池', count: data.value.email_pools.invalid || 0, rule: '验证不通过' },
  { pool: '禁止发送池', count: data.value.email_pools.suppressed || 0, rule: '黑名单或人工禁止' },
])

const activeStep = computed(() => {
  if (data.value.metrics.valid_emails || data.value.metrics.review_emails) return 5
  if (data.value.metrics.emails) return 4
  if (data.value.metrics.contacts) return 3
  if (data.value.metrics.brands) return 2
  return 0
})

async function load() {
  loading.value = true
  try {
    data.value = (await api.get('/dashboard')).data
    updatedAt.value = new Date().toLocaleString()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
