import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as rbacApi from '@/api/rbac'
import { getToken, setToken } from '@/api/http'
import type { MeResponse, User, MenuNode } from '@/types/rbac'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getToken())
  const me = ref<MeResponse | null>(null)
  const loading = ref(false)

  const isAuthenticated = computed<boolean>(() => token.value !== null && me.value !== null)
  const isSuperadmin = computed<boolean>(() => me.value?.is_superadmin ?? false)
  const user = computed<User | null>(() => me.value?.user ?? null)
  const permissions = computed<string[]>(() => me.value?.permissions ?? [])
  const menus = computed<MenuNode[]>(() => me.value?.menus ?? [])

  function hasPermission(code: string): boolean {
    if (isSuperadmin.value) return true
    return permissions.value.includes(code) || permissions.value.includes('*')
  }

  async function login(username: string, password: string): Promise<void> {
    loading.value = true
    try {
      const r = await rbacApi.login(username, password)
      token.value = r.data.access_token
      setToken(r.data.access_token)
      await loadMe()
    } finally {
      loading.value = false
    }
  }

  async function loadMe(): Promise<void> {
    if (!token.value) {
      me.value = null
      return
    }
    const r = await rbacApi.fetchMe()
    me.value = r.data
  }

  function logout(): void {
    token.value = null
    me.value = null
    setToken(null)
  }

  return {
    token,
    me,
    loading,
    isAuthenticated,
    isSuperadmin,
    user,
    permissions,
    menus,
    hasPermission,
    login,
    loadMe,
    logout,
  }
})
