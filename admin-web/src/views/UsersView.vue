<template>
  <el-card>
    <div class="toolbar">
      <el-input
        v-model="q"
        placeholder="按用户名搜索"
        clearable
        style="width: 220px"
        @keyup.enter="reload"
      />
      <el-button type="primary" @click="reload">查询</el-button>
    </div>

    <el-table v-loading="loading" :data="items" border stripe>
      <el-table-column prop="id" label="ID" width="230" show-overflow-tooltip />
      <el-table-column prop="username" label="用户名" min-width="120" />
      <el-table-column prop="points_balance" label="积分余额" width="110">
        <template #default="{ row }">
          <span class="points">{{ row.points_balance }}</span>
        </template>
      </el-table-column>
      <el-table-column label="角色" width="110">
        <template #default="{ row }">
          <el-tag :type="row.role === 'admin' ? 'danger' : 'info'">
            {{ row.role === 'admin' ? '管理员' : '普通用户' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button link type="warning" @click="openAdjust(row)">调整积分</el-button>
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      class="pager"
      layout="total, prev, pager, next, sizes"
      :total="total"
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :page-sizes="[10, 20, 50]"
      @current-change="load"
      @size-change="load"
    />

    <el-dialog v-model="adjustDialog.visible" title="调整积分" width="420px">
      <el-form label-width="90px">
        <el-form-item label="用户">
          <span>{{ adjustDialog.user?.username }}</span>
          <el-tag class="balance" size="small">当前 {{ adjustDialog.user?.points_balance }}</el-tag>
        </el-form-item>
        <el-form-item label="变动量">
          <el-input-number v-model="adjustDialog.change" :step="100" />
          <div class="hint">正数发放积分，负数扣减积分</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="adjustDialog.note" maxlength="255" placeholder="选填，将记入积分流水" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="adjustDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="adjustDialog.saving" @click="saveAdjust">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialog.visible" title="用户详情" width="640px">
      <template v-if="detailDialog.user">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="ID">{{ detailDialog.user.id }}</el-descriptions-item>
          <el-descriptions-item label="用户名">{{ detailDialog.user.username }}</el-descriptions-item>
          <el-descriptions-item label="积分余额">{{ detailDialog.user.points_balance }}</el-descriptions-item>
          <el-descriptions-item label="角色">{{ detailDialog.user.role }}</el-descriptions-item>
        </el-descriptions>
        <div class="section-title">最近积分流水</div>
        <el-table :data="detailDialog.user.recent_ledger ?? []" border size="small">
          <el-table-column label="变动" width="80">
            <template #default="{ row }">{{ formatPoints(row.change) }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="原因" width="120" />
          <el-table-column prop="balance_after" label="变动后余额" width="110" />
          <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip />
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { adjustPoints, getUser, listUsers, type AdminUser } from '../api/admin'
import { formatDateTime, formatPoints } from '../utils'

const loading = ref(false)
const items = ref<AdminUser[]>([])
const total = ref(0)
const q = ref('')
const page = ref(1)
const pageSize = ref(20)

const adjustDialog = reactive({
  visible: false,
  saving: false,
  change: 0,
  note: '',
  user: null as AdminUser | null,
})

const detailDialog = reactive({ visible: false, user: null as AdminUser | null })

async function load() {
  loading.value = true
  try {
    const data = await listUsers({ q: q.value || undefined, page: page.value, page_size: pageSize.value })
    items.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  load()
}

function openAdjust(row: AdminUser) {
  adjustDialog.user = row
  adjustDialog.change = 0
  adjustDialog.note = ''
  adjustDialog.visible = true
}

async function saveAdjust() {
  if (!adjustDialog.user || adjustDialog.change === 0) {
    ElMessage.warning('变动量不能为 0')
    return
  }
  adjustDialog.saving = true
  try {
    await adjustPoints(adjustDialog.user.id, adjustDialog.change, adjustDialog.note)
    ElMessage.success('积分已调整')
    adjustDialog.visible = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调整失败')
  } finally {
    adjustDialog.saving = false
  }
}

async function openDetail(row: AdminUser) {
  try {
    detailDialog.user = await getUser(row.id)
    detailDialog.visible = true
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载失败')
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.points {
  font-weight: 600;
  color: var(--el-color-warning);
}
.balance {
  margin-left: 8px;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
.section-title {
  font-weight: 600;
  margin: 16px 0 8px;
}
</style>
