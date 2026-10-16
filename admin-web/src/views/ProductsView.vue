<template>
  <el-card>
    <div class="toolbar">
      <el-input
        v-model="query.q"
        placeholder="按商品名搜索"
        clearable
        style="width: 220px"
        @keyup.enter="reload"
      />
      <el-select
        v-model="query.category_id"
        placeholder="全部分类"
        clearable
        style="width: 180px"
      >
        <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
      </el-select>
      <el-button type="primary" @click="reload">查询</el-button>
      <div class="spacer" />
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>
        新增商品
      </el-button>
    </div>

    <el-table v-loading="loading" :data="items" border stripe>
      <el-table-column prop="id" label="ID" width="230" show-overflow-tooltip />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="points_price" label="积分价" width="90" />
      <el-table-column prop="stock" label="库存" width="80">
        <template #default="{ row }">
          <el-tag :type="row.stock > 0 ? 'info' : 'danger'">{{ row.stock }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="分类" width="120">
        <template #default="{ row }">{{ categoryName(row.category_id) }}</template>
      </el-table-column>
      <el-table-column label="上架" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.is_active" @change="(v: string | number | boolean) => onToggleStatus(row, Boolean(v))" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="warning" @click="openStock(row)">库存</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      class="pager"
      layout="total, prev, pager, next, sizes"
      :total="total"
      v-model:current-page="query.page"
      v-model:page-size="query.page_size"
      :page-sizes="[10, 20, 50]"
      @current-change="load"
      @size-change="load"
    />

    <el-dialog v-model="editDialog.visible" :title="editDialog.isCreate ? '新增商品' : '编辑商品'" width="520px">
      <el-form :model="editDialog.form" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="editDialog.form.name" />
        </el-form-item>
        <el-form-item label="描述" required>
          <el-input v-model="editDialog.form.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="积分价" required>
          <el-input-number v-model="editDialog.form.points_price" :min="1" />
        </el-form-item>
        <el-form-item v-if="editDialog.isCreate" label="库存" required>
          <el-input-number v-model="editDialog.form.stock" :min="0" />
        </el-form-item>
        <el-form-item label="分类" required>
          <el-select v-model="editDialog.form.category_id" style="width: 100%">
            <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="图片链接">
          <el-input v-model="editDialog.form.image_url" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="editDialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="stockDialog.visible" title="调整库存" width="380px">
      <el-form label-width="90px">
        <el-form-item label="商品">
          <span>{{ stockDialog.product?.name }}</span>
        </el-form-item>
        <el-form-item label="变动量">
          <el-input-number v-model="stockDialog.delta" :step="1" />
          <div class="hint">正数增加库存，负数减少库存</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="stockDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="stockDialog.saving" @click="saveStock">确定</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  adjustStock,
  createProduct,
  listCategories,
  listProducts,
  setProductStatus,
  updateProduct,
  type Category,
  type Product,
} from '../api/admin'

const loading = ref(false)
const items = ref<Product[]>([])
const total = ref(0)
const categories = ref<Category[]>([])
const query = reactive({ q: '', category_id: '', page: 1, page_size: 20 })

const editDialog = reactive({
  visible: false,
  isCreate: false,
  saving: false,
  productId: '',
  form: {
    name: '',
    description: '',
    points_price: 100,
    stock: 0,
    category_id: '',
    image_url: '',
  },
})

const stockDialog = reactive({
  visible: false,
  saving: false,
  delta: 0,
  product: null as Product | null,
})

function categoryName(categoryId: string): string {
  return categories.value.find((c) => c.id === categoryId)?.name ?? categoryId
}

async function load() {
  loading.value = true
  try {
    const data = await listProducts(query)
    items.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function reload() {
  query.page = 1
  load()
}

async function loadCategories() {
  const data = await listCategories()
  categories.value = data.items
}

function openCreate() {
  editDialog.isCreate = true
  editDialog.productId = ''
  editDialog.form = {
    name: '',
    description: '',
    points_price: 100,
    stock: 0,
    category_id: categories.value[0]?.id ?? '',
    image_url: '',
  }
  editDialog.visible = true
}

function openEdit(row: Product) {
  editDialog.isCreate = false
  editDialog.productId = row.id
  editDialog.form = {
    name: row.name,
    description: row.description,
    points_price: row.points_price,
    stock: row.stock,
    category_id: row.category_id,
    image_url: row.image_url ?? '',
  }
  editDialog.visible = true
}

async function save() {
  const form = editDialog.form
  if (!form.name || !form.description || !form.category_id) {
    ElMessage.warning('请填写完整：名称、描述与分类必填')
    return
  }
  editDialog.saving = true
  try {
    if (editDialog.isCreate) {
      await createProduct({ ...form, image_url: form.image_url || null })
      ElMessage.success('商品已创建')
    } else {
      await updateProduct(editDialog.productId, {
        name: form.name,
        description: form.description,
        points_price: form.points_price,
        category_id: form.category_id,
        image_url: form.image_url || null,
      })
      ElMessage.success('商品已更新')
    }
    editDialog.visible = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    editDialog.saving = false
  }
}

async function onToggleStatus(row: Product, value: boolean) {
  try {
    await ElMessageBox.confirm(
      value ? `确认上架「${row.name}」？` : `确认下架「${row.name}」？下架后用户端不可见。`,
      '提示',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await setProductStatus(row.id, value)
    ElMessage.success(value ? '已上架' : '已下架')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '操作失败')
  }
}

function openStock(row: Product) {
  stockDialog.product = row
  stockDialog.delta = 0
  stockDialog.visible = true
}

async function saveStock() {
  if (!stockDialog.product || stockDialog.delta === 0) {
    ElMessage.warning('变动量不能为 0')
    return
  }
  stockDialog.saving = true
  try {
    await adjustStock(stockDialog.product.id, stockDialog.delta)
    ElMessage.success('库存已调整')
    stockDialog.visible = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调整失败')
  } finally {
    stockDialog.saving = false
  }
}

onMounted(async () => {
  await loadCategories()
  await load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}
.spacer {
  flex: 1;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
</style>
