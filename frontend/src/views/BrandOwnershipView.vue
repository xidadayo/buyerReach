<template>
  <div class="ownership-view">
    <div class="ownership-toolbar">
      <div class="toolbar-copy">
        <p class="section-kicker">BRAND OWNERSHIP</p>
        <h2>按品牌归属管理数据</h2>
        <span>有效联系人必须至少关联一个真实性已验证的邮箱；仅有人名和职位的记录保留为待补全线索。</span>
      </div>
      <div class="toolbar-side">
        <div class="overview-metrics" aria-label="归属数据概览">
          <div><b>{{ overview.brands }}</b><span>品牌</span></div>
          <div><b>{{ overview.contacts }}</b><span>有效联系人</span></div>
          <div><b>{{ overview.emails }}</b><span>已验证邮箱</span></div>
        </div>
        <el-button :loading="loading" @click="load">刷新归属关系</el-button>
      </div>
    </div>

    <div v-loading="loading" class="ownership-list">
      <el-empty v-if="!loading && !items.length && !sharedEmailRelationships.length" description="暂无本组或共享品牌数据" />
      <section v-for="brand in items" :key="brand.id" class="brand-branch">
        <article class="brand-node">
          <div class="brand-identity">
            <div class="node-mark brand-mark">B</div>
            <div class="brand-main">
              <div class="brand-title"><h3>{{ brand.name }}</h3><el-tag size="small">{{ brand.status }}</el-tag></div>
              <p>{{ brand.company_name || brand.category || '未补充公司或品类信息' }}</p>
              <a v-if="brand.primary_website" :href="brand.primary_website" target="_blank" rel="noreferrer">{{ brand.primary_website }}</a>
            </div>
          </div>
          <div class="brand-totals">
            <div class="brand-metric valid-total"><b>{{ brand.valid_contact_count ?? brand.contact_count }}</b><span>有效联系人</span></div>
            <div class="brand-metric"><b>{{ brand.discovered_contact_count ?? brand.contacts?.length ?? 0 }}</b><span>已发现</span></div>
            <div class="brand-metric"><b>{{ brand.verified_email_count ?? 0 }}</b><span>已验证邮箱</span></div>
          </div>
          <div class="brand-actions">
            <el-button
              v-if="brand.contacts?.length || brand.brand_emails?.length"
              class="details-button"
              :aria-controls="`brand-details-${brand.id}`"
              :aria-expanded="isExpanded(brand.id)"
              @click="toggleExpanded(brand.id)"
            >
              {{ isExpanded(brand.id) ? '收起明细' : `展开明细 (${childCount(brand)})` }}
            </el-button>
            <el-button class="manage-button" type="primary" plain @click="emit('open', 'brands')">管理品牌</el-button>
          </div>
        </article>

        <div
          v-if="(brand.contacts?.length || brand.brand_emails?.length) && isExpanded(brand.id)"
          :id="`brand-details-${brand.id}`"
          class="ownership-children"
        >
          <div v-if="brand.brand_emails?.length" class="contact-branch generic-branch">
            <article class="contact-node generic-node">
              <div class="node-mark email-mark">@</div>
              <div class="contact-main">
                <div class="contact-title"><h4>品牌通用邮箱</h4><span>官网 / 公共渠道</span></div>
                <small>归属：{{ brand.name }}</small>
              </div>
              <el-button link type="primary" @click="emit('open', 'emails')">管理邮箱</el-button>
            </article>
            <div class="email-rail">
              <span class="email-label">邮箱</span>
              <div class="email-list">
                <div v-for="email in brand.brand_emails" :key="email.id" class="email-chip">
                  <span class="email-dot" />
                  <span>{{ email.address }}</span>
                  <el-tag size="small" :type="emailTag(email.status)">{{ email.status }}</el-tag>
                </div>
              </div>
            </div>
          </div>

          <div v-for="contact in brand.contacts" :key="contact.id" class="contact-branch">
            <article class="contact-node">
              <div class="node-mark contact-mark">C</div>
              <div class="contact-main">
                <div class="contact-title">
                  <h4>{{ contact.full_name }}</h4>
                  <span>{{ contact.title || '未填写职位' }}</span>
                </div>
                <small>归属：{{ brand.name }}</small>
              </div>
              <el-tag class="contact-status" size="small" :type="contact.is_valid ? 'success' : 'danger'">
                {{ contact.is_valid ? '有效联系人' : contactStatus(contact.status) }}
              </el-tag>
              <el-button link type="primary" @click="emit('open', 'contacts')">管理联系人</el-button>
            </article>
            <div class="email-rail">
              <span class="email-label">邮箱</span>
              <div v-if="contact.emails?.length" class="email-list">
                <div v-for="email in contact.emails" :key="email.id" class="email-chip">
                  <span class="email-dot" />
                  <span>{{ email.address }}</span>
                  <el-tag size="small" :type="emailTag(email.status)">{{ email.status }}</el-tag>
                </div>
              </div>
              <span v-else class="empty-email invalid-lead">无邮箱：该记录是待补全线索，不计入有效联系人</span>
            </div>
          </div>
        </div>
        <div v-else-if="!brand.contacts?.length && !brand.brand_emails?.length" class="no-children">该品牌尚未关联联系人或邮箱。</div>
      </section>

      <section v-if="sharedEmailRelationships.length" class="shared-email-section">
        <div class="shared-email-heading">
          <div>
            <p class="section-kicker">SHARED EMAILS</p>
            <h3>共享给本组的邮箱</h3>
            <span>这些邮箱可供本组使用；下方仅展示其关联联系人和公司/品牌，不会将关联数据变为本组自有。</span>
          </div>
          <el-tag type="info">{{ sharedEmailRelationships.length }} 条组共享</el-tag>
        </div>
        <div class="shared-email-list">
          <article v-for="relation in sharedEmailRelationships" :key="relation.email.id" class="shared-email-row">
            <div class="node-mark email-mark">@</div>
            <div class="shared-email-main">
              <strong>{{ relation.email.address }}</strong>
              <span>
                联系人：{{ relation.contact?.name || '未关联联系人' }}
                <template v-if="relation.brand"> · 公司/品牌：{{ relation.brand.company_name || relation.brand.name }}</template>
              </span>
            </div>
            <el-tag size="small" type="info">组共享</el-tag>
            <el-button link type="primary" @click="emit('open', 'emails')">查看邮箱</el-button>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'

const emit = defineEmits<{ (event: 'open', tab: string): void }>()
const loading = ref(false)
const items = ref<any[]>([])
const sharedEmailRelationships = ref<any[]>([])
const expandedBrandIds = ref<Set<string | number>>(new Set())
const overview = computed(() => items.value.reduce((total, brand) => ({
  brands: total.brands + 1,
  contacts: total.contacts + Number(brand.valid_contact_count ?? brand.contact_count ?? 0),
  emails: total.emails + Number(brand.verified_email_count ?? 0),
}), { brands: 0, contacts: 0, emails: 0 }))

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/brands/hierarchy', { params: { page_size: 100 } })
    items.value = data.items || []
    sharedEmailRelationships.value = data.shared_email_relationships || []
    const visibleIds = new Set(items.value.map((brand) => brand.id))
    expandedBrandIds.value = new Set(
      [...expandedBrandIds.value].filter((brandId) => visibleIds.has(brandId)),
    )
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

function childCount(brand: any) {
  return Number(brand.contacts?.length || 0) + Number(brand.brand_emails?.length || 0)
}

function isExpanded(brandId: string | number) {
  return expandedBrandIds.value.has(brandId)
}

function toggleExpanded(brandId: string | number) {
  const next = new Set(expandedBrandIds.value)
  if (next.has(brandId)) next.delete(brandId)
  else next.add(brandId)
  expandedBrandIds.value = next
}

function emailTag(status: string) {
  if (status === 'valid') return 'success'
  if (status === 'invalid' || status === 'do_not_contact') return 'danger'
  if (status === 'risky' || status === 'unknown') return 'warning'
  return 'info'
}

function contactStatus(status: string) {
  if (status === 'pending_verification') return '邮箱待验证'
  if (status === 'pending_review') return '邮箱待复核'
  return '无效联系人'
}

onMounted(load)
</script>

<style scoped>
.ownership-view {
  display: grid;
  gap: 18px;
}

.ownership-toolbar {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: flex-end;
  padding: 2px 2px 16px;
  border-bottom: 1px solid #e5edf5;
}

.toolbar-copy { min-width: 0; }
.section-kicker { margin: 0 0 4px; color: #3d86ce; font-size: 10px; font-weight: 800; letter-spacing: .16em; }
.ownership-toolbar h2 { margin: 0; color: #173551; font-size: 20px; line-height: 1.35; }
.toolbar-copy > span { display: block; margin-top: 5px; color: #718096; font-size: 12px; line-height: 1.6; }
.toolbar-side { display: flex; flex: 0 0 auto; gap: 14px; align-items: center; }
.overview-metrics { display: flex; overflow: hidden; border: 1px solid #dbe7f1; border-radius: 10px; background: #fff; }
.overview-metrics > div { display: flex; min-width: 78px; flex-direction: column; align-items: center; padding: 7px 12px; }
.overview-metrics > div + div { border-left: 1px solid #e5edf5; }
.overview-metrics b { color: #173b5a; font-size: 15px; line-height: 1.1; }
.overview-metrics span { margin-top: 3px; color: #8090a1; font-size: 10px; }

.ownership-list { min-height: 150px; column-count: 2; column-gap: 14px; }
.shared-email-section { break-inside: avoid; margin-bottom: 14px; padding: 16px; border: 1px solid #cfe0ee; border-radius: 13px; background: #fbfdff; box-shadow: 0 7px 20px rgba(35, 74, 109, .06); }
.shared-email-heading { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; padding-bottom: 12px; border-bottom: 1px solid #e3edf5; }
.shared-email-heading h3 { margin: 0; color: #173551; font-size: 16px; }
.shared-email-heading span { display: block; margin-top: 4px; color: #718096; font-size: 12px; line-height: 1.5; }
.shared-email-list { display: grid; gap: 8px; margin-top: 12px; }
.shared-email-row { display: flex; gap: 10px; align-items: center; padding: 10px; border: 1px solid #e2ebf3; border-radius: 9px; background: #fff; }
.shared-email-main { display: grid; min-width: 0; flex: 1; gap: 3px; }
.shared-email-main strong, .shared-email-main span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shared-email-main strong { color: #173551; font-size: 13px; }
.shared-email-main span { color: #718096; font-size: 11px; }
.brand-branch { position: relative; display: inline-block; overflow: hidden; width: 100%; margin-bottom: 14px; break-inside: avoid; border: 1px solid #cfe0ee; border-radius: 13px; background: #fff; box-shadow: 0 7px 20px rgba(35, 74, 109, .08); }
.brand-branch::before { position: absolute; z-index: 2; top: 0; bottom: 0; left: 0; width: 4px; background: linear-gradient(180deg, #2e83d5 0%, #62afea 42%, #9b70dc 100%); content: ''; }
.brand-node { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px 12px; align-items: center; padding: 13px 14px 12px 18px; background: linear-gradient(110deg, #eef7ff 0%, #f7fbff 56%, #fff 100%); }
.brand-identity { display: flex; grid-column: 1; grid-row: 1; min-width: 0; gap: 12px; align-items: center; }
.node-mark { display: grid; flex: 0 0 auto; place-items: center; width: 34px; height: 34px; border-radius: 10px; color: #fff; font-size: 12px; font-weight: 800; box-shadow: 0 4px 10px rgba(32, 89, 139, .15); }
.brand-mark { background: linear-gradient(135deg, #2677c9, #5ca9ed); }
.contact-mark { background: linear-gradient(135deg, #119775, #46c99f); }
.email-mark { background: linear-gradient(135deg, #8d61d8, #b58aef); }
.brand-main, .contact-main { min-width: 0; }
.brand-title, .contact-title { display: flex; min-width: 0; gap: 8px; align-items: center; }
.brand-title h3, .contact-title h4 { overflow: hidden; margin: 0; color: #173551; text-overflow: ellipsis; white-space: nowrap; }
.brand-title h3 { font-size: 16px; }
.contact-title h4 { font-size: 14px; }
.brand-main p { overflow: hidden; margin: 4px 0; color: #667b90; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.brand-main a { display: block; overflow: hidden; color: #397fc4; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.brand-totals { display: grid; grid-column: 1 / -1; grid-row: 2; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; padding-top: 1px; }
.brand-metric { display: flex; min-width: 0; flex-direction: column; align-items: center; padding: 5px 7px; border: 1px solid #dfebf5; border-radius: 8px; background: rgba(255, 255, 255, .78); }
.brand-metric b { color: #173b5a; font-size: 14px; line-height: 1.1; }
.brand-metric span { margin-top: 3px; color: #72879a; font-size: 10px; white-space: nowrap; }
.brand-metric.valid-total { border-color: #d2eadf; background: #edf9f3; }
.brand-actions { display: flex; grid-column: 2; grid-row: 1; gap: 8px; align-items: center; }
.brand-actions :deep(.el-button + .el-button) { margin-left: 0; }
.details-button, .manage-button { min-width: 82px; }

.ownership-children { display: grid; grid-template-columns: 1fr; gap: 0; padding: 0 0 0 4px; border-top: 1px solid #dce8f2; background: #fff; }
.contact-branch { position: relative; min-width: 0; overflow: hidden; border: 0; border-radius: 0; background: #fff; box-shadow: none; }
.contact-branch + .contact-branch { border-top: 1px solid #e3ece9; }
.contact-node { display: flex; min-height: 44px; gap: 8px; align-items: center; padding: 9px 13px; background: linear-gradient(90deg, #f4fbf8 0%, #fbfefc 70%, #fff 100%); }
.contact-main { flex: 1; }
.contact-title span { overflow: hidden; color: #60778c; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.contact-main small { display: block; overflow: hidden; margin-top: 3px; color: #8b9aaa; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.contact-status { flex: 0 0 auto; }
.email-rail { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px; align-items: start; min-height: 31px; padding: 8px 13px 9px 55px; border-top: 1px solid #e7f0ec; background: #fff; }
.email-label { padding-top: 4px; color: #8798a8; font-size: 10px; font-weight: 700; }
.email-list { display: flex; min-width: 0; flex-wrap: wrap; gap: 6px; }
.email-chip { display: inline-flex; max-width: 100%; gap: 5px; align-items: center; padding: 4px 7px; border: 1px solid #e6dcf7; border-radius: 7px; color: #4b3f62; font-size: 11px; background: #fff; overflow-wrap: anywhere; }
.email-dot { width: 5px; height: 5px; flex: 0 0 auto; border-radius: 50%; background: #9a72dc; }
.email-chip :deep(.el-tag) { height: 17px; flex: 0 0 auto; padding: 0 4px; font-size: 10px; }
.empty-email, .no-children { color: #8a9aae; font-size: 11px; }
.empty-email { padding-top: 3px; line-height: 1.45; }
.no-children { margin-left: 4px; padding: 14px 55px; border-top: 1px solid #dce8f2; background: #fbfdff; }
.invalid-lead { color: #c45656; }
.generic-branch { grid-column: 1 / -1; }
.generic-node { background: linear-gradient(90deg, #f8f4ff 0%, #fcfaff 70%, #fff 100%); }
.generic-branch .email-rail { border-color: #eee5f8; }

@media (max-width: 1180px) {
  .ownership-toolbar { align-items: flex-start; }
  .toolbar-side { flex-direction: column-reverse; align-items: flex-end; }
  .ownership-list { column-count: 1; }
}

@media (max-width: 720px) {
  .ownership-view { gap: 14px; }
  .ownership-toolbar { flex-direction: column; gap: 12px; }
  .toolbar-side { width: 100%; flex-direction: row; justify-content: space-between; align-items: center; }
  .overview-metrics { flex: 1; }
  .overview-metrics > div { min-width: 0; flex: 1; padding-inline: 7px; }
  .brand-node { display: flex; flex-wrap: wrap; gap: 11px; padding: 12px; }
  .brand-identity { width: calc(100% - 96px); flex: 1 1 auto; }
  .brand-actions { margin-left: auto; }
  .brand-totals { order: 3; width: 100%; grid-template-columns: repeat(3, 1fr); }
  .brand-metric { min-width: 0; padding-inline: 5px; }
  .ownership-children { gap: 0; padding: 0 0 0 4px; }
  .contact-node { flex-wrap: wrap; }
  .contact-main { flex: 1 1 calc(100% - 46px); }
  .contact-status { margin-left: 44px; }
  .email-rail { padding-left: 56px; }
  .no-children { padding: 16px 58px; }
  .shared-email-row { flex-wrap: wrap; }
  .shared-email-main { flex-basis: calc(100% - 46px); }
}

@media (max-width: 480px) {
  .toolbar-side { align-items: stretch; flex-direction: column; }
  .brand-node { align-items: flex-start; }
  .brand-identity { width: 100%; }
  .brand-actions { width: calc(100% - 46px); flex-wrap: wrap; margin-left: 46px; }
  .contact-node :deep(.el-button) { margin-left: 44px; }
  .email-rail { grid-template-columns: 1fr; padding-left: 56px; }
  .email-label { display: none; }
}
</style>
