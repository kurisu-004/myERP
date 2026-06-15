<template>
  <div class="dashboard">
    <el-row :gutter="16">
      <el-col :span="6" v-for="card in cards" :key="card.label">
        <div class="stat-card" :style="{ borderTopColor: card.color }">
          <div class="stat-icon" :style="{ background: card.color }">
            <el-icon :size="22">
              <component :is="card.icon" />
            </el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-label">{{ card.label }}</div>
            <div class="stat-value">{{ card.value }}</div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import { Box, Document, Money, User } from '@element-plus/icons-vue'

interface StatCard {
  label: string
  value: string
  icon: Component
  color: string
}

const cards: StatCard[] = [
  { label: '零件总数', value: '1,286', icon: Box, color: '#1e4d8b' },
  { label: '今日入库', value: '32', icon: Document, color: '#2c6cb8' },
  { label: '本月销售额', value: '¥ 128,560', icon: Money, color: '#4a8fd6' },
  { label: '活跃用户', value: '24', icon: User, color: '#7eb0e3' },
]
</script>

<style lang="scss" scoped>
.dashboard { padding: 4px; }
.stat-card {
  background: #fff;
  border-radius: 6px;
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  border-top: 3px solid;
  box-shadow: var(--shadow-sm);
}
.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}
.stat-label { color: var(--text-secondary); font-size: 13px; }
.stat-value { color: var(--text-primary); font-size: 22px; font-weight: 600; margin-top: 4px; }
</style>
