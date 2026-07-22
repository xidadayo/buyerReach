import axios from 'axios'
import { useAuth } from '../stores/auth'

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

// Inject JWT token on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('buyerreach_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle 401 by trying refresh, then redirecting to login
let refreshPromise: Promise<boolean> | null = null

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retry) {
      error.config._retry = true
      if (!refreshPromise) {
        const { refreshAccessToken } = useAuth()
        refreshPromise = refreshAccessToken()
      }
      const ok = await refreshPromise
      refreshPromise = null
      if (ok) {
        const token = localStorage.getItem('buyerreach_token')
        error.config.headers.Authorization = `Bearer ${token}`
        return api(error.config)
      }
      // Refresh failed, redirect to login
      localStorage.removeItem('buyerreach_token')
      localStorage.removeItem('buyerreach_refresh_token')
      window.location.href = '/login'
      return Promise.reject(error)
    }
    const detail = error.response?.data?.detail
    const validationMessage = Array.isArray(detail)
      ? detail.map((item: any) => {
          const field = Array.isArray(item?.loc) ? item.loc.filter((part: unknown) => part !== 'body').join('.') : ''
          return `${field ? `${field}: ` : ''}${item?.msg || '参数格式错误'}`
        }).join('；')
      : ''
    const message = typeof detail === 'string'
      ? detail
      : validationMessage || error.message || '请求失败'
    return Promise.reject(new Error(message))
  },
)
