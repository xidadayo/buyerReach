<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-logo">
        <span class="logo-mark">B</span>
        <div>
          <strong>BuyerReach</strong>
          <small>买手通 V1</small>
        </div>
      </div>
      <h2 style="text-align: center; margin: 0 0 24px">登录</h2>
      <el-form @submit.prevent="doLogin">
        <el-form-item>
          <el-input
            v-model="email"
            placeholder="请输入邮箱"
            prefix-icon="el-icon"
            size="large"
            clearable
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="password"
            type="password"
            placeholder="请输入密码"
            show-password
            size="large"
            @keyup.enter="doLogin"
          />
        </el-form-item>
        <el-alert v-if="auth.state.error" :title="auth.state.error" type="error" show-icon :closable="false" style="margin-bottom: 16px" />
        <el-button
          type="primary"
          size="large"
          style="width: 100%"
          :loading="auth.state.loading"
          @click="doLogin"
        >
          登录
        </el-button>
      </el-form>
      <p class="login-hint">默认账号: admin@buyerreach.local / admin123</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ref } from 'vue'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const router = useRouter()
const email = ref('admin@buyerreach.local')
const password = ref('admin123')

async function doLogin() {
  const ok = await auth.login(email.value, password.value)
  if (ok) {
    router.replace({ path: '/' })
  }
}
</script>
