import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from './stores/auth'
import DashboardView from './views/DashboardView.vue'
import LoginView from './views/LoginView.vue'
import TasksView from './views/TasksView.vue'
import DataCenterView from './views/DataCenterView.vue'
import ReviewView from './views/ReviewView.vue'
import SettingsView from './views/SettingsView.vue'
import OutreachView from './views/OutreachView.vue'
import BatchExactBrandView from './views/BatchExactBrandView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView, meta: { guest: true } },
    { path: '/', component: DashboardView, meta: { title: '工作台' } },
    { path: '/tasks', component: TasksView, meta: { title: '搜索任务', permissions: ['tasks:read'] } },
    { path: '/data', component: DataCenterView, meta: { title: '客户数据中心', permissions: ['brands:read', 'contacts:read', 'emails:read'] } },
    { path: '/brands', redirect: (to) => ({ path: '/data', query: { ...to.query, tab: 'brands' } }) },
    { path: '/contacts', redirect: (to) => ({ path: '/data', query: { ...to.query, tab: 'contacts' } }) },
    { path: '/emails', redirect: (to) => ({ path: '/data', query: { ...to.query, tab: 'emails' } }) },
    { path: '/review', component: ReviewView, meta: { title: '审核与去重', permissions: ['imports:read', 'imports:execute', 'exports:execute', 'dedup:read', 'blacklist:read'] } },
    { path: '/batch-exact-brand', component: BatchExactBrandView, meta: { title: '批量精准品牌' } },
    { path: '/settings', component: SettingsView, meta: { title: '系统配置', permissions: ['settings:read', 'providers:read', 'organization_units:read', 'roles:read', 'users:read', 'tags:read', 'custom_fields:read', 'audit:read'] } },
    { path: '/outreach', component: OutreachView, meta: { title: '邮件触达', permissions: ['outreach:read'] } },
  ],
})

router.beforeEach(async (to) => {
  const { isAuthenticated, fetchMe } = useAuth()

  // Allow access to login page without auth
  if (to.meta.guest) {
    if (isAuthenticated()) {
      return { path: '/' }
    }
    return true
  }

  // All other routes require authentication
  if (!isAuthenticated()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // Fetch user info if not loaded
  if (!useAuth().state.user) {
    const ok = await fetchMe()
    if (!ok) {
      return { path: '/login' }
    }
  }

  const permissions = Array.isArray(to.meta.permissions) ? to.meta.permissions as string[] : []
  if (permissions.length && !useAuth().hasAnyPermission(permissions)) {
    return { path: '/' }
  }

  return true
})
