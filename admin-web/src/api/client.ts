const TOKEN_KEY = 'shop_admin_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string> | undefined) ?? {}),
  }
  const token = getToken()
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  const response = await fetch(path, { ...options, headers })
  if (response.status === 401) {
    clearToken()
    window.location.hash = '#/login'
    throw new ApiError(401, 'unauthorized', '登录已过期，请重新登录')
  }
  const data = (await response.json().catch(() => null)) as (T & { message?: string; code?: string }) | null
  if (!response.ok) {
    throw new ApiError(
      response.status,
      data?.code ?? 'unknown',
      data?.message ?? `请求失败（${response.status}）`,
    )
  }
  return data as T
}
