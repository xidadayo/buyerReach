import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from './stores/auth'
import DashboardView from './views/DashboardView.vue'
import LoginView from './views/LoginView.vue'
import TasksView from './views/TasksView.vue'
import DiscoveryCandidatesView from './views/DiscoveryCandidatesView.vue'
import DataCenterView from './views/DataCenterView.vue'
import ReviewView from './views/ReviewView.vue'
import SettingsView from './views/SettingsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView, meta: { guest: true } },
    { path: '/', component: DashboardView, meta: { title: '工作台' } },
    { path: '/tasks', component: TasksView, meta: { title: '搜索任务' } },
    { path: '/candidates', component: DiscoveryCandidatesView, meta: { title: '品牌候选池' } },
    { path: '/data', component: DataCenterView, meta: { title: '客户数据中心' } },
    { path: '/brands', redirect: (to) => ({ path: '/data', query: { ...to.query, tab: 'brands' } }) },
    { path: '/contacts', redirect: (to) => ({ path: '/data', query: { ...to.query, tab: 'contacts' } }) },
    { path: '/emails', redirect: (to) => ({ path: '/data', query: { ...to.query, tab: 'emails' } }) },
    { path: '/review', component: ReviewView, meta: { title: '审核与去重' } },
    { path: '/settings', component: SettingsView, meta: { title: '系统配置' } },
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

  return true
})
