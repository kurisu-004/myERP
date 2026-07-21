<template>
  <div class="login-page">
    <div class="login-card">
      <div class="card-header">
        <el-icon :size="32" color="#4a8fd6"><Box /></el-icon>
        <h1>myERP</h1>
        <p>零件加工订单管理系统</p>
      </div>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="doLogin">
        <el-form-item label="账号" prop="username">
          <el-input v-model="form.username" placeholder="用户名" :prefix-icon="UserIcon" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码" show-password :prefix-icon="LockIcon" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" class="submit-btn">
          {{ loading ? '登录中...' : '登 录' }}
        </el-button>
      </el-form>
      <p v-if="error" class="error-msg">{{ error }}</p>
    </div>
    <div class="login-page-footer">
      <BeianFooter />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, Box } from '@element-plus/icons-vue'
import { useAuthSession } from '@/composables/useAuthSession'
import BeianFooter from '@/components/BeianFooter.vue'

const UserIcon = User
const LockIcon = Lock

const router = useRouter()
const { login } = useAuthSession()

const formRef = ref()
const loading = ref(false)
const error = ref('')
const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function doLogin() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  error.value = ''
  try {
    const u = await login(form.username, form.password)
    // 按角色自动跳。优先级：MANAGER → /dashboard；
    // CLERK / CNC_PROGRAMMER → /parts（编程员默认筛选 PROGRAMMING）；
    // INSPECTOR → /inspection/pending（品检员的日常入口，2026-07-20 PR-I）；
    // 纯 SHELF_ACCOUNT → /scan/badge。
    if (u.roles.includes('MANAGER')) {
      router.replace('/dashboard')
    } else if (u.roles.includes('CLERK') || u.roles.includes('CNC_PROGRAMMER')) {
      router.replace('/parts')
    } else if (u.roles.includes('INSPECTOR')) {
      router.replace('/inspection/pending')
    } else if (u.roles.includes('SHELF_ACCOUNT')) {
      router.replace('/scan/badge')
    } else {
      error.value = '账号无任何可用角色'
    }
  } catch (e: any) {
    error.value = e?.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.login-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a365d 0%, #2d5a87 100%);
}

.login-page-footer {
  margin-top: 24px;
}
.login-card {
  width: 380px;
  max-width: calc(100vw - 24px);
  background: #fff;
  border-radius: 8px;
  padding: 40px 36px 32px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);

  @include until(sm) {
    padding: 32px 22px 24px;
  }
}
.card-header {
  text-align: center;
  margin-bottom: 28px;
  h1 { margin: 8px 0 4px; font-size: 22px; color: #1a365d; }
  p { color: #909399; font-size: 13px; margin: 0; }
}
.submit-btn { width: 100%; margin-top: 8px; }
.error-msg { color: #f56c6c; font-size: 13px; text-align: center; margin-top: 12px; }
</style>
