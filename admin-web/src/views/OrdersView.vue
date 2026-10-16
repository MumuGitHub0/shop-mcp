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
      <el-table-column prop="id" label="订单号" min-width="220" show-overflow-tooltip />
      <el-table-column prop="user_id" label="用户 ID" width="200" show-overflow-tooltip />
      <el-table-column prop="points_total" label="积分合计" width="100" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag>{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="下单时间" width="180">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row.id)">详情</el-button>
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

    <el-dialog v-model="detailDialog.visible" title="订单详情" width="680px">
      <template v-if="detailDialog.order">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="订单号">{{ detailDialog.order.id }}</el-descriptions-item>
          <el-descriptions-item label="用户 ID">{{ detailDialog.order.user_id }}</el-descriptions-item>
          <el-descriptions-item label="积分合计">{{ detailDialog.order.points_total }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabel(detailDialog.order.status) }}</el-descriptions-item>
          <el-descriptions-item label="收货地址" :span="2">
            {{ detailDialog.order.shipping_address }}
          </el-descriptions-item>
          <el-descriptions-item label="下单时间" :span="2">
            {{ formatDateTime(detailDialog.order.created_at) }}
          </el-descriptions-item>
        </el-descriptions>
        <div class="section-title">商品明细（含下单时价格快照）</div>
        <el-table :data="detailDialog.order.items" border size="small">
          <el-table-column prop="product_id" label="商品 ID" min-width="180" show-overflow-tooltip />
          <el-table-column prop="name" label="名称" min-width="120" />
          <el-table-column prop="points_price" label="积分单价" width="90" />
          <el-table-column prop="quantity" label="数量" width="70" />
          <el-table-column prop="subtotal" label="小计" width="80" />
        </el-table>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getOrder, listOrders, type Order } from '../api/admin'
import { formatDateTime } from '../utils'

const loading = ref(false)
const items = ref<Order[]>([])
const total = ref(0)
const userId = ref('')
const page = ref(1)
const pageSize = ref(20)

const detailDialog = reactive({ visible: false, order: null as Order | null })

const STATUS_LABELS: Record<string, string> = {
  created: '已创建',
  paid: '已支付',
  shipped: '已发货',
  completed: '已完成',
  cancelled: '已取消',
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

async function load() {
  loading.value = true
  try {
    const data = await listOrders({
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

async function openDetail(orderId: string) {
  try {
    detailDialog.order = await getOrder(orderId)
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
.section-title {
  font-weight: 600;
  margin: 16px 0 8px;
}
</style>
