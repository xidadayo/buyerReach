<template>
  <div v-if="auth.state.user || route.path === '/login'" class="app-root">
    <!-- Login page rendered by router -->
    <router-view v-if="route.path === '/login'" />

    <!-- Main app layout -->
    <el-container v-else class="app-shell">
      <el-aside width="232px" class="sidebar">
        <div class="logo">
          <span class="logo-mark">B</span>
          <div>
            <strong>BuyerReach</strong>
            <small>买手通 V1</small>
          </div>
        </div>
        <div class="nav-label">General</div>
          <el-menu router :default-active="route.path" background-color="#162230" text-color="#b7c4d2" active-text-color="#ffffff">
            <el-menu-item index="/">工作台</el-menu-item>
            <el-menu-item index="/tasks">搜索任务</el-menu-item>
            <el-menu-item index="/candidates">品牌候选池</el-menu-item>
            <el-menu-item index="/data">客户数据中心</el-menu-item>
          </el-menu>
        <div class="nav-label">Admin</div>
        <el-menu router :default-active="route.path" background-color="#162230" text-color="#b7c4d2" active-text-color="#ffffff">
          <el-menu-item index="/review">审核与去重</el-menu-item>
          <el-menu-item index="/settings">系统配置</el-menu-item>
        </el-menu>
      </el-aside>
      <el-container>
        <el-header class="topbar">
          <div>
            <span class="breadcrumb">Home / BuyerReach</span>
            <strong>客户数据基础平台</strong>
          </div>
          <div class="top-actions">
            <span class="system-pill">All systems operational</span>
            <el-dropdown trigger="click">
              <div class="user-chip">
                <el-avatar :size="28" style="background: #0ea5e9">{{ userInitial }}</el-avatar>
                <div class="user-meta">
                  <span class="user-name">{{ auth.state.user?.name }}</span>
                  <span class="user-role">{{ auth.state.user?.role || 'viewer' }}</span>
                </div>
▼
              </div>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item disabled>
                    {{ auth.state.user?.email }}
                  </el-dropdown-item>
                  <el-dropdown-item disabled>
                    组织: {{ auth.state.user?.organization_name || '-' }}
                  </el-dropdown-item>
                  <el-dropdown-item divided @click="changePassword">修改密码</el-dropdown-item>
                  <el-dropdown-item divided @click="doLogout">退出登录</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button type="primary" @click="$router.push('/tasks')">创建任务</el-button>
          </div>
        </el-header>
        <el-main>
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </div>

  <!-- Loading state while checking auth -->
  <div v-else class="app-loading">
    <span class="loading-icon" style="font-size: 36px">⟳</span>
    <p>加载中...</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuth } from './stores/auth'

const auth = useAuth()
const route = useRoute()
const router = useRouter()

const userInitial = computed(() => {
  const name = auth.state.user?.name || 'U'
  return name.charAt(0).toUpperCase()
})

async function doLogout() {
  await auth.logout()
  router.replace({ path: '/login' })
}

async function changePassword() {
  try {
    const { value } = await ElMessageBox.prompt('请输入新密码 (至少8位)', '修改密码', {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      inputType: 'password',
      inputValidator: (val: string) => {
        if (!val || val.length < 8) return '密码至少8个字符'
        return true
      },
    } as any)
    if (value) {
      const currentPwd = (await ElMessageBox.prompt('请输入当前密码', '验证身份', {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        inputType: 'password',
      } as any)).value
      if (currentPwd) {
        await auth.changePassword(currentPwd, value)
        ElMessage.success('密码已修改，请重新登录')
        await doLogout()
      }
    }
  } catch {
    // user cancelled
  }
}
</script>
