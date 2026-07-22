<!--
  司机扫码台待送货一览（PR-G 2026-07-22 新增）。

  路径：/scan/delivery-note-pickup
  入口：ScanActionPicker.vue 选「送 货」后跳转（仅 driver worker.work_type='送货司机'）。
  用户角色：任意已登录（service 层不再二次校验 driver 身份，因为 SHELF_ACCOUNT
  共账号就能扫码台；真实 driver 校验在 pickup / pickup-scan 内部做）。
-->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Van } from '@element-plus/icons-vue'

import { listPickupPending } from '@/api/deliveryNote'
import {
  DELIVERY_NOTE_STATUS_LABEL,
  DELIVERY_NOTE_STATUS_TAG,
  type DeliveryNoteOut,
  type DeliveryNoteStatus,
} from '@/types/deliveryNote'

const router = useRouter()
const loading = ref(false)
const items = ref<DeliveryNoteOut[]>([])

async function fetchList() {
  loading.value = true
  try {
    items.value = await listPickupPending()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(fetchList)

function openPickup(n: DeliveryNoteOut) {
  router.push(`/scan/delivery-note-pickup/${n.id}`)
}
</script>

<template>
  <div class="pickup-list">
    <h3 class="page-title">待送货一览（SUBMITTED）</h3>
    <p class="hint">选择一条送货单进入扫码领取。</p>

    <el-table
      v-loading="loading"
      :data="items"
      stripe
      border
      :empty-text="loading ? '加载中' : '当前没有待送货的送货单'"
    >
      <el-table-column prop="delivery_note_no" label="单号" width="190" />
      <el-table-column label="客户" min-width="220">
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).customer_path
            ?? (scope.row as DeliveryNoteOut).customer_name ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="scope">
          <el-tag
            :type="DELIVERY_NOTE_STATUS_TAG[(scope.row as DeliveryNoteOut).status] || 'info'"
            size="small"
          >
            {{ DELIVERY_NOTE_STATUS_LABEL[(scope.row as DeliveryNoteOut).status] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="part_count" label="零件数" width="100" align="center" />
      <el-table-column label="提交时间" width="200">
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).submitted_at
            ? new Date((scope.row as DeliveryNoteOut).submitted_at!).toLocaleString() : '—' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="scope">
          <el-button type="primary" @click="openPickup(scope.row as DeliveryNoteOut)">
            <el-icon><Van /></el-icon>
            进入扫码领取
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.pickup-list { padding: 16px; }
.page-title { font-size: 18px; font-weight: 600; margin-bottom: 8px; }
.hint { color: #909399; margin-bottom: 16px; font-size: 13px; }
</style>
