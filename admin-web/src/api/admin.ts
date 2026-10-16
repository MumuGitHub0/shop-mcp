import { request } from './client'

export interface Product {
  id: string
  name: string
  description: string
  points_price: number
  stock: number
  category_id: string
  is_active: boolean
  image_url: string | null
}

export interface Category {
  id: string
  name: string
  is_active: boolean
}

export interface LedgerEntry {
  id: string
  user_id: string
  reason: string
  change: number
  order_id: string | null
  balance_after: number
  note: string | null
  created_at: string
}

export interface AdminUser {
  id: string
  username: string
  points_balance: number
  role: string
  recent_ledger?: LedgerEntry[]
}

export interface OrderItem {
  product_id: string
  name: string
  points_price: number
  quantity: number
  subtotal: number
}

export interface Order {
  id: string
  user_id: string
  status: string
  points_total: number
  shipping_address: string
  items: OrderItem[]
  created_at: string
}

export interface Page<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export interface LoginResponse {
  access_token: string
  token_type: string
}

export interface ProductPayload {
  name: string
  description: string
  points_price: number
  stock: number
  category_id: string
  image_url?: string | null
}

export interface ProductUpdatePayload {
  name?: string
  description?: string
  points_price?: number
  category_id?: string
  image_url?: string | null
}

export async function adminLogin(username: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>('/admin/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export async function listProducts(params: {
  q?: string
  category_id?: string
  page?: number
  page_size?: number
}): Promise<Page<Product>> {
  const query = new URLSearchParams()
  if (params.q) query.set('q', params.q)
  if (params.category_id) query.set('category_id', params.category_id)
  query.set('page', String(params.page ?? 1))
  query.set('page_size', String(params.page_size ?? 20))
  return request<Page<Product>>(`/admin/api/v1/products?${query}`)
}

export async function createProduct(payload: ProductPayload): Promise<Product> {
  return request<Product>('/admin/api/v1/products', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateProduct(productId: string, payload: ProductUpdatePayload): Promise<Product> {
  return request<Product>(`/admin/api/v1/products/${productId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function setProductStatus(productId: string, isActive: boolean): Promise<Product> {
  return request<Product>(`/admin/api/v1/products/${productId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ is_active: isActive }),
  })
}

export async function adjustStock(productId: string, delta: number): Promise<Product> {
  return request<Product>(`/admin/api/v1/products/${productId}/stock`, {
    method: 'PATCH',
    body: JSON.stringify({ delta }),
  })
}

export async function listCategories(): Promise<{ items: Category[] }> {
  return request<{ items: Category[] }>('/admin/api/v1/categories')
}

export async function createCategory(name: string): Promise<Category> {
  return request<Category>('/admin/api/v1/categories', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export async function updateCategory(
  categoryId: string,
  payload: { name?: string; is_active?: boolean },
): Promise<Category> {
  return request<Category>(`/admin/api/v1/categories/${categoryId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function listUsers(params: {
  q?: string
  page?: number
  page_size?: number
}): Promise<Page<AdminUser>> {
  const query = new URLSearchParams()
  if (params.q) query.set('q', params.q)
  query.set('page', String(params.page ?? 1))
  query.set('page_size', String(params.page_size ?? 20))
  return request<Page<AdminUser>>(`/admin/api/v1/users?${query}`)
}

export async function getUser(userId: string): Promise<AdminUser> {
  return request<AdminUser>(`/admin/api/v1/users/${userId}`)
}

export async function adjustPoints(
  userId: string,
  change: number,
  note?: string,
): Promise<AdminUser> {
  return request<AdminUser>(`/admin/api/v1/users/${userId}/points/adjust`, {
    method: 'POST',
    body: JSON.stringify({ change, note: note || undefined }),
  })
}

export async function listOrders(params: {
  user_id?: string
  page?: number
  page_size?: number
}): Promise<Page<Order>> {
  const query = new URLSearchParams()
  if (params.user_id) query.set('user_id', params.user_id)
  query.set('page', String(params.page ?? 1))
  query.set('page_size', String(params.page_size ?? 20))
  return request<Page<Order>>(`/admin/api/v1/orders?${query}`)
}

export async function getOrder(orderId: string): Promise<Order> {
  return request<Order>(`/admin/api/v1/orders/${orderId}`)
}

export async function listLedger(params: {
  user_id?: string
  page?: number
  page_size?: number
}): Promise<Page<LedgerEntry>> {
  const query = new URLSearchParams()
  if (params.user_id) query.set('user_id', params.user_id)
  query.set('page', String(params.page ?? 1))
  query.set('page_size', String(params.page_size ?? 20))
  return request<Page<LedgerEntry>>(`/admin/api/v1/ledger?${query}`)
}
