<template>
  <section class="data-center">
    <div class="data-hero">
      <div>
        <p class="eyebrow">BUYERREACH · DATA HUB</p>
        <h1>客户数据中心</h1>
        <p class="hero-copy">在同一工作台管理品牌、买手联系人与可用邮箱，数据链路一目了然。</p>
      </div>
    </div>

    <div class="workspace-panel">
      <el-tabs v-model="activeTab" class="data-tabs" @tab-change="syncTab">
        <el-tab-pane name="ownership">
          <template #label><span class="tab-label"><b>归属关系</b><small>品牌主档视图</small></span></template>
          <BrandOwnershipView @open="openTab" />
        </el-tab-pane>
        <el-tab-pane name="brands">
          <template #label><span class="tab-label"><b>品牌</b><small>发现与审核</small></span></template>
          <BrandsView />
        </el-tab-pane>
        <el-tab-pane name="contacts">
          <template #label><span class="tab-label"><b>联系人</b><small>职位与归属</small></span></template>
          <ContactsView />
        </el-tab-pane>
        <el-tab-pane name="emails">
          <template #label><span class="tab-label"><b>邮箱池</b><small>验证与导出</small></span></template>
          <EmailsView />
        </el-tab-pane>
      </el-tabs>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BrandsView from './BrandsView.vue'
import BrandOwnershipView from './BrandOwnershipView.vue'
import ContactsView from './ContactsView.vue'
import EmailsView from './EmailsView.vue'

const route = useRoute()
const router = useRouter()
const tabs = ['ownership', 'brands', 'contacts', 'emails']
const activeTab = ref(tabFromRoute())

function tabFromRoute() {
  const requested = typeof route.query.tab === 'string' ? route.query.tab : ''
  return tabs.includes(requested) ? requested : 'ownership'
}

watch(() => route.query.tab, () => {
  activeTab.value = tabFromRoute()
})

function syncTab(tab: string | number) {
  openTab(String(tab))
}

function openTab(nextTab: string) {
  if (nextTab === route.query.tab) return
  router.replace({ query: { ...route.query, tab: nextTab } })
}

</script>

<style scoped>
.data-center { display: grid; gap: 14px; }
.data-hero { display: flex; justify-content: space-between; gap: 20px; align-items: center; padding: 18px 22px; border-radius: 14px; color: #fff; background: radial-gradient(circle at 88% 18%, rgba(77, 171, 247, .3), transparent 28%), linear-gradient(120deg, #16314f, #24547d); box-shadow: 0 8px 20px rgba(23, 55, 89, .14); }
.eyebrow { margin: 0 0 8px; font-size: 11px; font-weight: 700; letter-spacing: .16em; color: #9ed2ff; }
.data-hero h1 { margin: 0; font-size: 25px; letter-spacing: -.03em; }.hero-copy { margin: 6px 0 0; color: #d5e7f8; font-size: 13px; }
.workspace-panel { min-width: 0; padding: 6px 14px 14px; border: 1px solid #e2ebf4; border-radius: 13px; background: #fff; box-shadow: 0 5px 16px rgba(20, 43, 69, .05); }
.data-tabs { min-width: 0; }
.data-tabs :deep(.el-tabs__content), .data-tabs :deep(.el-tab-pane) { min-width: 0; width: 100%; }
.data-tabs :deep(.el-tabs__header) { margin: 0 0 12px; }.data-tabs :deep(.el-tabs__nav-wrap::after) { display: none; }.data-tabs :deep(.el-tabs__nav) { gap: 3px; padding: 3px; border-radius: 9px; background: #f2f6fa; }.data-tabs :deep(.el-tabs__item) { height: 48px; padding: 0 17px; border-radius: 7px; }.data-tabs :deep(.el-tabs__item.is-active) { background: #fff; box-shadow: 0 2px 6px rgba(37, 74, 107, .1); }.data-tabs :deep(.el-tabs__active-bar) { display: none; }
.tab-label { display: grid; gap: 2px; padding-top: 7px; line-height: 1.08; text-align: left; }.tab-label b { font-size: 14px; }.tab-label small { font-size: 10px; color: #94a3b8; }.data-tabs :deep(.is-active .tab-label small) { color: #4c91d9; }
.data-tabs :deep(.page-heading) { margin: 0 0 10px; }.data-tabs :deep(.page-title) { font-size: 18px; }
@media (max-width: 900px) { .data-hero { align-items: flex-start; flex-direction: column; }.workspace-panel { padding: 6px 10px 12px; }.data-tabs :deep(.el-tabs__item) { padding: 0 10px; } }
</style>
