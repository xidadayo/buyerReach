import { reactive } from 'vue'
import { api } from '../api/client'

const TOKEN_KEY = 'buyerreach_token'
const REFRESH_KEY = 'buyerreach_refresh_token'

interface UserInfo {
  id: string
  email: string
  name: string
  role: string | null
  organization_name: string | null
  organization_unit_id: string | null
  organization_unit_name: string | null
  status: string
  permissions: string[]
  data_scopes: Record<string, string> | null
  permission_version: number | null
}

interface AuthState {
  token: string | null
  refreshToken: string | null
  user: UserInfo | null
  loading: boolean
  error: string | null
}

const state = reactive<AuthState>({
  token: localStorage.getItem(TOKEN_KEY),
  refreshToken: localStorage.getItem(REFRESH_KEY),
  user: null,
  loading: false,
  error: null,
})

function setTokens(access: string, refresh: string) {
  state.token = access
  state.refreshToken = refresh
  localStorage.setItem(TOKEN_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
}

function clearAuth() {
  state.token = null
  state.refreshToken = null
  state.user = null
  state.error = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

async function login(email: string, password: string): Promise<boolean> {
  state.loading = true
  state.error = null
  try {
    const { data } = await api.post('/auth/login', { email, password })
    setTokens(data.access_token, data.refresh_token)
    state.user = data.user
    return true
  } catch (error) {
    state.error = (error as Error).message
    return false
  } finally {
    state.loading = false
  }
}

async function refreshAccessToken(): Promise<boolean> {
  if (!state.refreshToken) return false
  try {
    const { data } = await api.post('/auth/refresh', { refresh_token: state.refreshToken })
    state.token = data.access_token
    localStorage.setItem(TOKEN_KEY, data.access_token)
    return true
  } catch {
    clearAuth()
    return false
  }
}

async function fetchMe(): Promise<boolean> {
  if (!state.token) return false
  try {
    const { data } = await api.get('/auth/me')
    state.user = data
    return true
  } catch {
    clearAuth()
    return false
  }
}

async function logout() {
  state.loading = true
  try {
    clearAuth()
  } finally {
    state.loading = false
  }
}

function changePassword(currentPassword: string, newPassword: string) {
  return api.post('/auth/change-password', { current_password: currentPassword, new_password: newPassword })
}

export function hasPermission(permission: string): boolean {
  if (!state.user?.permissions) return false
  return state.user.permissions.includes('admin:*') || state.user.permissions.includes(permission)
}

export function hasAnyPermission(permissions: string[]): boolean {
  return permissions.some(hasPermission)
}

export function dataScope(resource: string): string {
  return state.user?.data_scopes?.[resource] || 'self'
}

export function useAuth() {
  return {
    state,
    login,
    logout,
    fetchMe,
    refreshAccessToken,
    changePassword,
    isAuthenticated: () => !!state.token,
    hasPermission,
    hasAnyPermission,
    dataScope,
  }
}
