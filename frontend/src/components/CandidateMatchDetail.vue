<template>
  <div class="match-detail">
    <section class="detail-block">
      <h4>本次搜索</h4>
      <div v-if="taskContext">
        <div class="detail-line">
          <span class="detail-label">搜索任务</span>
          <span>{{ taskContext.task_name || '未命名任务' }}</span>
          <span v-if="taskContext.task_status" class="muted">（{{ taskContext.task_status }}）</span>
        </div>
        <div v-if="taskContext.original_prompt" class="detail-line">
          <span class="detail-label">搜索目标</span>
          <span>{{ taskContext.original_prompt }}</span>
        </div>
        <div class="detail-line">
          <span class="detail-label">目标概念</span>
          <span v-if="targetConcepts.length">
            <el-tag v-for="concept in targetConcepts" :key="concept.id || concept.source_text" size="small" style="margin-right: 6px">
              {{ concept.source_text || concept.normalized_label }}
            </el-tag>
          </span>
          <span v-else class="muted">该任务未记录目标概念</span>
        </div>
        <div v-if="filterText" class="detail-line">
          <span class="detail-label">筛选条件</span>
          <span class="muted">{{ filterText }}</span>
        </div>
      </div>
      <div v-else class="muted">未找到来源任务信息（任务可能已删除）</div>
    </section>

    <section class="detail-block">
      <h4>概念匹配结果</h4>
      <div v-if="matchedConcepts.length">
        <div class="detail-line" v-for="(match, index) in matchedConcepts" :key="index">
          <el-tag size="small" type="success">{{ match.company_concept || '未知概念' }}</el-tag>
          <span class="match-relation">{{ relationshipLabel(match.relationship) }}</span>
          <span>目标「{{ conceptLabel(match.target_concept_id) }}」</span>
          <span class="muted">置信度 {{ match.confidence ?? 0 }}% · {{ evidenceLevelLabel(match.evidence_level) }}</span>
        </div>
      </div>
      <div v-else class="muted">暂无命中概念</div>
      <div v-if="uncoveredConcepts.length" class="detail-line" style="margin-top: 6px">
        <span class="detail-label">未覆盖目标</span>
        <el-tag v-for="concept in uncoveredConcepts" :key="concept.id || concept.source_text" size="small" type="info" style="margin-right: 6px">
          {{ concept.source_text || concept.normalized_label }}
        </el-tag>
      </div>
      <div v-if="conflictingConcepts.length" class="detail-line" style="margin-top: 6px">
        <span class="detail-label">冲突概念</span>
        <el-tag v-for="(match, index) in conflictingConcepts" :key="index" size="small" type="danger" style="margin-right: 6px">
          {{ match.company_concept || '未知概念' }}
        </el-tag>
      </div>
    </section>

    <section class="detail-block">
      <h4>六维评分</h4>
      <div v-if="dimensionRows.length" class="dimension-grid">
        <div v-for="row in dimensionRows" :key="row.key" class="dimension-row">
          <span class="dimension-label">{{ row.label }}</span>
          <el-progress :percentage="row.percent" :stroke-width="10" :show-text="false" style="flex: 1" />
          <span class="dimension-value">{{ row.value }}/{{ row.cap }}</span>
        </div>
      </div>
      <div v-else class="muted">尚未形成维度评分</div>
      <div v-if="penalties.length" class="detail-line" style="margin-top: 6px">
        <span class="detail-label">扣分项</span>
        <span v-for="(penalty, index) in penalties" :key="index" class="penalty">
          {{ reasonLabel(penalty.code) }}（{{ penalty.points }}）
        </span>
      </div>
    </section>

    <section class="detail-block">
      <h4>判定与证据</h4>
      <div class="detail-line">
        <span class="detail-label">评估状态</span>
        <span>{{ evaluationText }}</span>
      </div>
      <div v-if="reasonCodes.length" class="detail-line">
        <span class="detail-label">判定原因</span>
        <span>{{ reasonCodes.map(reasonLabel).join('；') }}</span>
      </div>
      <div v-if="evidenceList.length" class="detail-line">
        <span class="detail-label">证据来源</span>
        <span>
          <div v-for="(item, index) in evidenceList" :key="index">
            {{ evidenceSourceLabel(item.source_type) }}
            <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer">{{ item.url }}</a>
            <span v-if="item.excerpt" class="muted"> — {{ String(item.excerpt).slice(0, 120) }}</span>
          </div>
        </span>
      </div>
      <div class="detail-line">
        <span class="detail-label">评分版本</span>
        <span class="muted">
          policy {{ candidate.scoring_policy_version || evaluation.policy_version || '-' }} ·
          prompt {{ candidate.prompt_version || '-' }} ·
          schema {{ candidate.evidence_schema_version || evaluation.evidence_schema_version || '-' }}
        </span>
      </div>
      <div class="detail-line">
        <span class="detail-label">资料完整度</span>
        <span class="muted">{{ completenessCount }}/7 项（仅表示资料覆盖，不代表匹配度）</span>
      </div>
      <div class="detail-line">
        <span class="detail-label">推荐操作</span>
        <strong>{{ recommendedAction }}</strong>
      </div>
      <template v-if="candidate.industry_enrichment_status === 'failed'">
        <div class="detail-line">
          <span class="detail-label">补充失败</span>
          <span>
            {{ failureMessage }}
            <span v-if="failureResetAt" class="muted">（配额重置时间：{{ failureResetAt }}）</span>
          </span>
        </div>
        <div v-if="candidate.industry_enrichment_error" class="detail-line">
          <span class="detail-label">技术详情</span>
          <span class="muted error-detail">{{ candidate.industry_enrichment_error }}</span>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { safeEvaluationStatus } from '../api/compat'

interface Concept {
  id?: string
  source_text?: string
  normalized_label?: string
}

interface TaskContext {
  task_id?: string
  task_name?: string
  task_status?: string
  original_prompt?: string
  target_concepts?: Concept[]
  categories?: string[]
  countries?: string[]
}

const props = defineProps<{
  candidate: Record<string, any>
  taskContext: TaskContext | null
}>()

const evaluation = computed<Record<string, any>>(() =>
  props.candidate.match_evaluation && typeof props.candidate.match_evaluation === 'object'
    ? props.candidate.match_evaluation
    : {},
)

const profile = computed<Record<string, any>>(() =>
  props.candidate.company_profile && typeof props.candidate.company_profile === 'object'
    ? props.candidate.company_profile
    : {},
)

const targetConcepts = computed<Concept[]>(() =>
  Array.isArray(props.taskContext?.target_concepts) ? props.taskContext!.target_concepts! : [],
)

const matchedConcepts = computed<Record<string, any>[]>(() =>
  Array.isArray(evaluation.value.matched_concepts) ? evaluation.value.matched_concepts : [],
)

const conflictingConcepts = computed<Record<string, any>[]>(() =>
  Array.isArray(evaluation.value.conflicting_concepts) ? evaluation.value.conflicting_concepts : [],
)

const uncoveredConcepts = computed<Concept[]>(() => {
  const covered = new Set(matchedConcepts.value.map(match => match.target_concept_id))
  return targetConcepts.value.filter(concept => concept.id && !covered.has(concept.id))
})

const filterText = computed(() => {
  const parts: string[] = []
  if (props.taskContext?.categories?.length) parts.push(`品类：${props.taskContext.categories.join('、')}`)
  if (props.taskContext?.countries?.length) parts.push(`国家：${props.taskContext.countries.join('、')}`)
  return parts.join('；')
})

const DIMENSIONS: Array<{ key: string; label: string; cap: number }> = [
  { key: 'product_fit', label: '产品匹配', cap: 40 },
  { key: 'industry_fit', label: '行业匹配', cap: 20 },
  { key: 'business_type_fit', label: '商业类型', cap: 15 },
  { key: 'country_fit', label: '国家', cap: 10 },
  { key: 'evidence_quality', label: '证据质量', cap: 10 },
  { key: 'category_coverage', label: '品类覆盖', cap: 5 },
]

const dimensionRows = computed(() => {
  const scores = evaluation.value.dimension_scores
  if (!scores || typeof scores !== 'object' || !Object.keys(scores).length) return []
  return DIMENSIONS.map(dimension => {
    const value = Number(scores[dimension.key] ?? 0)
    return {
      ...dimension,
      value,
      percent: Math.min(100, Math.round((value / dimension.cap) * 100)),
    }
  })
})

const penalties = computed<Record<string, any>[]>(() =>
  Array.isArray(evaluation.value.penalties) ? evaluation.value.penalties : [],
)

const reasonCodes = computed<string[]>(() =>
  Array.isArray(evaluation.value.reason_codes) ? evaluation.value.reason_codes : [],
)

const evidenceList = computed<Record<string, any>[]>(() =>
  Array.isArray(profile.value.evidence) ? profile.value.evidence : [],
)

const REASON_LABELS: Record<string, string> = {
  no_company_level_evidence: '缺少公司级证据，无法形成正式评分',
  bounded_concept_scope: '在限定目标概念范围内完成匹配',
  concept_match_unavailable: '概念匹配服务暂不可用',
  excluded_industry_without_direct_product_evidence: '属于排除行业且无直接产品证据',
  concept_conflict: '存在与目标冲突的概念',
}

function reasonLabel(code: unknown) {
  const key = String(code ?? '')
  return REASON_LABELS[key] || key || '未知原因'
}

function relationshipLabel(value: unknown) {
  return (
    {
      exact: '精确匹配',
      synonym: '同义匹配',
      child: '子类匹配',
      descendant: '子类匹配',
      parent: '上位匹配',
      related: '相关匹配',
      conflicting: '冲突',
      unknown: '未知关系',
    }[String(value ?? '')] || '未知关系'
  )
}

function evidenceLevelLabel(value: unknown) {
  return (
    {
      provider_query: '搜索查询',
      provider_company: 'Provider 公司资料',
      company_description: '公司描述',
      official_website: '官网证据',
      official_product_page: '官网产品页',
    }[String(value ?? '')] || '其他证据'
  )
}

function evidenceSourceLabel(value: unknown) {
  return (
    {
      official_website: '官网',
      hunter_company_enrichment: 'Hunter 公司资料',
      hunter_company_enrichment_ai: 'Hunter + AI',
      official_website_ai: '官网 + AI',
    }[String(value ?? '')] || String(value ?? '证据')
  )
}

function conceptLabel(id: unknown) {
  const concept = targetConcepts.value.find(item => item.id === id)
  return concept?.source_text || concept?.normalized_label || String(id ?? '目标概念')
}

const evaluationText = computed(() => {
  const status = safeEvaluationStatus(props.candidate.evaluation_status)
  return (
    {
      pending: '待评估',
      running: '评估中',
      insufficient_data: '证据不足',
      completed: '已完成正式评估',
      failed: '评估失败，可重试',
      unknown: `未知状态（${String(props.candidate.evaluation_status ?? '空')}）`,
    }[status]
  )
})

const recommendedAction = computed(() => {
  const status = safeEvaluationStatus(props.candidate.evaluation_status)
  if (status === 'completed') {
    if (props.candidate.relevance_rating === 'A' || props.candidate.relevance_rating === 'B') {
      return '推荐精准丰富'
    }
    if (props.candidate.relevance_rating === 'C') return '建议人工查看证据后决定'
    return '不建议精准丰富，可考虑拒绝'
  }
  if (status === 'insufficient_data') return '建议补充行业证据后重新评估'
  if (status === 'failed') return '建议重试评估'
  if (status === 'running') return '评估进行中，请稍后查看'
  return '建议先补充行业完成评估'
})

const completenessCount = computed(() => {
  const candidate = props.candidate
  const products = Array.isArray(profile.value.products) ? profile.value.products : []
  const services = Array.isArray(profile.value.services) ? profile.value.services : []
  let count = 0
  if (candidate.website || candidate.domain) count += 1
  if (candidate.industry) count += 1
  if (candidate.industry_source) count += 1
  if (candidate.industry_confidence != null) count += 1
  if (products.length || services.length) count += 1
  if (evidenceList.value.length) count += 1
  if (matchedConcepts.value.length) count += 1
  return count
})

const failureMessage = computed(
  () => props.candidate.enrichment_failure?.message || '行业补充失败，可重试',
)
const failureResetAt = computed(() => props.candidate.enrichment_failure?.reset_at || '')
</script>

<style scoped>
.match-detail {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px 32px;
  padding: 12px 16px;
  background: var(--el-fill-color-lighter);
}

.detail-block h4 {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--el-text-color-primary);
}

.detail-line {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 13px;
  line-height: 1.8;
  flex-wrap: wrap;
}

.detail-label {
  flex: none;
  color: var(--el-text-color-secondary);
  min-width: 60px;
}

.match-relation {
  color: var(--el-color-primary);
  font-size: 12px;
}

.dimension-grid {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dimension-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dimension-label {
  flex: none;
  width: 60px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.dimension-value {
  flex: none;
  width: 48px;
  text-align: right;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.penalty {
  color: var(--el-color-danger);
  margin-right: 8px;
}

.error-detail {
  word-break: break-all;
  font-size: 12px;
}

@media (max-width: 768px) {
  .match-detail {
    grid-template-columns: minmax(0, 1fr);
    padding: 10px;
  }

  .detail-line {
    overflow-wrap: anywhere;
  }
}
</style>
