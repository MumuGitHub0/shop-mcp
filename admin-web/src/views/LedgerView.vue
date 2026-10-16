<template>
  <el-card>
    <div class="toolbar">
      <el-input
        v-model="userId"
        placeholder="按用户 ID 筛选"
        clearable
        style="width: 240px"
        @keyup.enter="reload"
      />
      <el-button type="primary" @click="reload">查询</el-button>
    </div>

    <el-table v-loading="loading" :data="items" border stripe>
      <el-table-column prop="user_id" label="用户 ID" width="200" show-overflow-tooltip />
      <el-table-column label="变动" width="90">
        <template #default="{ row }">
          <span :class="row.change > 0 ? 'increase' : 'decrease'">{{ formatPoints(row.change) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="reason" label="原因" width="130" />
      <el-table-column prop="balance_after" label="变动后余额" width="110" />
      <el-table-column prop="order_id" label="关联订单" min-width="180" show-overflow-tooltip />
      <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
      <el-table-column label="时间" width="180">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
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
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listLedger, type LedgerEntry } from '../api/admin'
import { formatDateTime, formatPoints } from '../utils'

const loading = ref(false)
const items = ref<LedgerEntry[]>([])
const total = ref(0)
const userId = ref('')
const page = ref(1)
const pageSize = ref(20)

async function load() {
  loading.value = true
  try {
    const data = await listLedger({
      user_id: userId.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
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
.increase {
  color: var(--el-color-success);
  font-weight: 600;
}
.decrease {
  color: var(--el-color-danger);
  font-weight: 600;
}
</style>
