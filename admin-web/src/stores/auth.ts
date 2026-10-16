import { defineStore } from 'pinia'
import { adminLogin } from '../api/admin'
import { clearToken, getToken, setToken } from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({ token: getToken() }),
  getters: {
    isLoggedIn: (state) => state.token !== '',
  },
  actions: {
    async login(username: string, password: string) {
      const data = await adminLogin(username, password)
      this.token = data.access_token
      setToken(data.access_token)
    },
    logout() {
      this.token = ''
      clearToken()
    },
  },
})
