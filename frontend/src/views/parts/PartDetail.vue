<!--
  PartDetail.vue

  /parts/:id  零件详情页。
  - 信息卡：序列号 / 图号 / 名称 / 状态 / 数量 / 客户 / 计划交期 / 实际送货 / 加急 / id
  - 所属装配件卡：仅当 part.assembly_id 非空时显示，含装配件基础信息 + 兄弟零件入口
  - 图纸 / 文件卡：FileListCard（PDF 内嵌预览 / STEP 等下载）
  - 历史卡：调用 GET /api/v1/parts/{id}/events，el-timeline 倒序展示
-->

<template>
  <div class="part-detail">
    
    <!-- 信息卡 -->
    <el-card shadow="never" class="info-card" v-loading="infoLoading">
      <template v-if="part">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="序列号">
            <span v-if="part.serial_no">{{ part.serial_no }}</span>
            <span v-else class="muted">—</span>
          </el-descriptions-item>
          <el-descriptions-item label="图号">{{ part.drawing_no }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusTagType(part.status)" effect="plain" size="small">
              {{ statusLabel(part.status) }}
            </el-tag>
          </el-descriptions-item>

          <el-descriptions-item label="名称" :span="3">{{ part.name }}</el-descriptions-item>

          <el-descriptions-item label="数量">{{ part.quantity }}</el-descriptions-item>
          <el-descriptions-item label="加急">
            <el-tag v-if="part.is_urgent" type="danger" effect="dark" size="small">加急</el-tag>
            <span v-else class="muted">否</span>
          </el-descriptions-item>
          <el-descriptions-item label="客户">
            <span v-if="part.customer_path">{{ part.customer_path }}</span>
            <span v-else-if="part.customer_name">{{ part.customer_name }}</span>
            <span v-else class="muted">—</span>
          </el-descriptions-item>

          <el-descriptions-item label="计划交期">{{ part.planned_delivery_date }}</el-descriptions-item>
          <el-descriptions-item label="实际送货">
            <span v-if="part.actual_delivery_date">{{ part.actual_delivery_date }}</span>
            <span v-else class="muted">—</span>
          </el-descriptions-item>
          <el-descriptions-item label="单据 ID">#{{ part.id }}</el-descriptions-item>
        </el-descriptions>
      </template>
    </el-card>

    <!-- 所属装配件（仅子零件） -->
    <el-card
      v-if="part && part.assembly_id != null"
      shadow="never"
      class="assembly-card"
      v-loading="assemblyLoading"
    >
      <template #header>
        <div class="card-header">
          <span class="card-title">
            <el-icon><Connection /></el-icon>
            <span>所属装配件</span>
          </span>
          <el-button
            link
            type="primary"
            size="small"
            @click="$router.push(`/assemblies/${part.assembly_id}`)"
          >
            查看装配件详情
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </template>
      <el-descriptions v-if="assemblyDetail" :column="3" border>
        <el-descriptions-item label="总图图号">
          <span class="mono">{{ assemblyDetail.assembly.drawing_no }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="装配体名称">
          {{ assemblyDetail.assembly.name }}
        </el-descriptions-item>
        <el-descriptions-item label="装配件状态">
          <el-tag :type="assemblyDetail.assembly.status === 'COMPLETED' ? 'success' : 'info'" size="small">
            {{ assemblyDetail.assembly.status }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="客户">
          {{ assemblyDetail.assembly.customer_path || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="子零件数">
          <el-tag type="info" size="small" effect="plain">
            {{ assemblyDetail.assembly.child_count }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="计划交期">
          {{ assemblyDetail.assembly.planned_delivery_date }}
        </el-descriptions-item>
      </el-descriptions>

      <div v-if="assemblyDetail" class="siblings">
        <div class="siblings-title">兄弟零件（点击跳转）</div>
        <div class="siblings-grid">
          <el-tag
            v-for="sib in assemblyDetail.children"
            :key="sib.id"
            :type="sib.id === part!.id ? 'primary' : 'info'"
            :effect="sib.id === part!.id ? 'dark' : 'plain'"
            class="sibling-chip"
            @click="$router.push(`/parts/${sib.id}`)"
          >
            <span class="sib-serial">{{ sib.serial_no || '—' }}</span>
            <span class="sib-name">{{ sib.drawing_no }}</span>
            <span class="sib-label">{{ sib.name }}</span>
          </el-tag>
        </div>
      </div>
    </el-card>

    <!-- 图纸 / 文件 -->
    <FileListCard
      :files="files"
      owner-type="part"
      :owner-id="partId"
      :show-upload="true"
      :show-delete="true"
      @refresh="fetchFiles"
    />

    <!-- 历史记录 -->
    <el-card shadow="never" class="history-card" v-loading="eventsLoading">
      <template #header>
        <div class="card-header">
          <span class="card-title">历史记录</span>
          <span v-if="events" class="event-count">共 {{ events.length }} 条</span>
        </div>
      </template>

      <div v-if="events && events.length > 0" class="timeline">
        <el-timeline>
          <el-timeline-item
            v-for="evt in events"
            :key="evt.id"
            :timestamp="formatDateTime(evt.created_at)"
            placement="top"
            :type="eventTagType(evt.event_type)"
            :hollow="evt.event_type !== 'CREATED'"
          >
            <div class="event-card">
              <div class="event-line-1">
                <el-tag :type="eventTagType(evt.event_type)" effect="dark" size="small">
                  {{ eventLabel(evt.event_type) }}
                </el-tag>
                <span v-if="evt.worker_name" class="worker-name">
                  <el-icon><User /></el-icon>
                  {{ evt.worker_name }}
                </span>
              </div>
              <div v-if="evt.from_status || evt.to_status" class="event-line-2">
                <span v-if="evt.from_status" class="status-pill">
                  {{ statusLabelOf(evt.from_status) }}
                </span>
                <el-icon v-if="evt.from_status && evt.to_status" class="arrow"><Right /></el-icon>
                <span v-if="evt.to_status" class="status-pill">
                  {{ statusLabelOf(evt.to_status) }}
                </span>
              </div>
              <div v-if="evt.drawing_code || evt.badge_code" class="event-line-3">
                <span v-if="evt.drawing_code">
                  <span class="meta-label">图纸</span>
                  <span class="meta-value">{{ evt.drawing_code }}</span>
                </span>
                <span v-if="evt.badge_code">
                  <span class="meta-label">工牌</span>
                  <span class="meta-value">{{ evt.badge_code }}</span>
                </span>
              </div>
              <div v-if="evt.note" class="event-note">备注：{{ evt.note }}</div>
            </div>
          </el-timeline-item>
        </el-timeline>
      </div>
      <el-empty v-else description="暂无历史记录" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Connection, Right, User } from '@element-plus/icons-vue'
import FileListCard from '@/components/FileListCard.vue'
import { getPart, listPartEvents, type PartItem, type PartEvent } from '@/api/parts'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  PART_EVENT_LABEL,
  PART_EVENT_TAG_TYPE,
  type OrderStatus,
  type PartEventType,
} from '@/types/parts'
import { getAssemblyForPart } from '@/api/assembly'
import type { AssemblyDetail } from '@/types/assembly'
import type { DrawingFileItem } from '@/types/file'
import { listPartFiles } from '@/api/assembly'

const route = useRoute()
// id 是后端 IdStr 序列化的字符串，雪花 ID 完整保留；不再 Number() 转回去
const partId = ref<string>(String(route.params.id ?? ''))

// ============ 数据 ============
const part = ref<PartItem | null>(null)
const events = ref<PartEvent[] | null>(null)
const files = ref<DrawingFileItem[]>([])
const assemblyDetail = ref<AssemblyDetail | null>(null)
const infoLoading = ref(false)
const eventsLoading = ref(false)
const filesLoading = ref(false)
const assemblyLoading = ref(false)

function statusLabel(s: OrderStatus): string {
  return ORDER_STATUS_LABEL[s] ?? s
}
function statusTagType(s: OrderStatus): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return ORDER_STATUS_TAG_TYPE[s] ?? 'info'
}
function statusLabelOf(s: string | null | undefined): string {
  if (!s) return ''
  return ORDER_STATUS_LABEL[s as OrderStatus] ?? s
}
function eventLabel(t: string): string {
  return PART_EVENT_LABEL[t as PartEventType] ?? t
}
function eventTagType(t: string): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return PART_EVENT_TAG_TYPE[t as PartEventType] ?? 'info'
}
function formatDateTime(iso: string): string {
  if (!iso) return ''
  const [d, t] = iso.split('T')
  if (!d) return iso
  if (!t) return d
  return `${d} ${t.slice(0, 5)}`
}

// ============ 拉取 ============
async function fetchPart(): Promise<void> {
  infoLoading.value = true
  try {
    part.value = await getPart(partId.value)
  } catch (e) {
    part.value = null
    ElMessage.error((e as Error).message ?? '加载零件失败')
  } finally {
    infoLoading.value = false
  }
}

async function fetchEvents(): Promise<void> {
  eventsLoading.value = true
  try {
    events.value = await listPartEvents(partId.value)
  } catch (e) {
    events.value = null
    ElMessage.error((e as Error).message ?? '加载历史记录失败')
  } finally {
    eventsLoading.value = false
  }
}

async function fetchFiles(): Promise<void> {
  filesLoading.value = true
  try {
    files.value = await listPartFiles(partId.value)
  } catch (e) {
    files.value = []
    ElMessage.error((e as Error).message ?? '加载文件列表失败')
  } finally {
    filesLoading.value = false
  }
}

async function fetchAssembly(): Promise<void> {
  if (!part.value || part.value.assembly_id == null) {
    assemblyDetail.value = null
    return
  }
  assemblyLoading.value = true
  try {
    assemblyDetail.value = await getAssemblyForPart(part.value.id)
  } catch (e) {
    assemblyDetail.value = null
    ElMessage.error((e as Error).message ?? '加载装配件信息失败')
  } finally {
    assemblyLoading.value = false
  }
}

onMounted(() => {
  void fetchPart()
  void fetchEvents()
  void fetchFiles()
})

// 路由参数变化时重新拉
watch(
  () => route.params.id,
  async (id) => {
    const s = String(id ?? '')
    if (!s) return
    partId.value = s
    assemblyDetail.value = null
    files.value = []
    await fetchPart()
    void fetchEvents()
    void fetchFiles()
    void fetchAssembly()
  },
)

// 拿到 part 之后再异步拉装配件信息（不阻塞主流程）
watch(
  () => part.value?.assembly_id,
  () => {
    void fetchAssembly()
  },
)
</script>

<style lang="scss" scoped>
.part-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sheet-title {
  background: linear-gradient(180deg, #ffffff 0%, #f3f6fb 100%);
  border: 1px solid var(--border-color);
  border-bottom: 2px solid var(--primary-color);
  padding: 14px 20px;
  font-size: 20px;
  font-weight: 700;
  color: var(--primary-color);
  letter-spacing: 2px;
  text-align: center;
  border-radius: 4px 4px 0 0;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-weight: 600;
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.event-count {
  color: var(--text-secondary);
  font-size: 13px;
}

.muted {
  color: var(--text-secondary);
}
.mono {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}

.assembly-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
}
.siblings {
  margin-top: 16px;
}
.siblings-title {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
.siblings-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.sibling-chip {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 4px;
  transition: transform 0.15s;
  &:hover {
    transform: translateY(-1px);
  }
}
.sib-serial {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-weight: 600;
}
.sib-name {
  color: var(--text-secondary);
  font-size: 12px;
}
.sib-label {
  font-size: 12px;
}

.history-card {
  .timeline {
    padding: 8px 0;
  }
  .event-card {
    background: #fff;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    padding: 10px 14px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .event-line-1 {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .worker-name {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--text-primary);
    font-size: 13px;
  }
  .event-line-2 {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text-regular);
    font-size: 13px;
  }
  .status-pill {
    padding: 1px 8px;
    border-radius: 10px;
    background: #f0f2f5;
    color: var(--text-primary);
    font-size: 12px;
  }
  .arrow {
    color: var(--text-secondary);
  }
  .event-line-3 {
    display: flex;
    gap: 16px;
    font-size: 12px;
    color: var(--text-secondary);
  }
  .meta-label {
    margin-right: 4px;
    color: var(--text-secondary);
  }
  .meta-value {
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    color: var(--text-primary);
  }
  .event-note {
    color: var(--text-regular);
    font-size: 13px;
    background: #fdf6ec;
    padding: 4px 8px;
    border-radius: 4px;
  }
}
</style>