<template>
  <el-card>
    <div class="toolbar">
      <span class="title">商品分类</span>
      <div class="spacer" />
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>
        新增分类
      </el-button>
    </div>

    <el-table v-loading="loading" :data="items" border stripe>
      <el-table-column prop="id" label="ID" width="230" show-overflow-tooltip />
      <el-table-column prop="name" label="名称" min-width="160" />
      <el-table-column label="启用" width="100">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'">
            {{ row.is_active ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openRename(row)">重命名</el-button>
          <el-button link :type="row.is_active ? 'danger' : 'success'" @click="onToggle(row)">
            {{ row.is_active ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog.visible" :title="dialog.isCreate ? '新增分类' : '重命名分类'" width="380px">
      <el-input v-model="dialog.name" placeholder="分类名称" maxlength="128" />
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createCategory, listCategories, updateCategory, type Category } from '../api/admin'

const loading = ref(false)
const items = ref<Category[]>([])

const dialog = reactive({ visible: false, isCreate: false, saving: false, categoryId: '', name: '' })

async function load() {
  loading.value = true
  try {
    const data = await listCategories()
    items.value = data.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  dialog.isCreate = true
  dialog.categoryId = ''
  dialog.name = ''
  dialog.visible = true
}

function openRename(row: Category) {
  dialog.isCreate = false
  dialog.categoryId = row.id
  dialog.name = row.name
  dialog.visible = true
}

async function save() {
  if (!dialog.name.trim()) {
    ElMessage.warning('请输入分类名称')
    return
  }
  dialog.saving = true
  try {
    if (dialog.isCreate) {
      await createCategory(dialog.name.trim())
      ElMessage.success('分类已创建')
    } else {
      await updateCategory(dialog.categoryId, { name: dialog.name.trim() })
      ElMessage.success('分类已更新')
    }
    dialog.visible = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    dialog.saving = false
  }
}

async function onToggle(row: Category) {
  try {
    await ElMessageBox.confirm(
      row.is_active ? `确认停用「${row.name}」？` : `确认启用「${row.name}」？`,
      '提示',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await updateCategory(row.id, { is_active: !row.is_active })
    ElMessage.success('操作成功')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '操作失败')
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
}
.title {
  font-weight: 600;
}
.spacer {
  flex: 1;
}
</style>
