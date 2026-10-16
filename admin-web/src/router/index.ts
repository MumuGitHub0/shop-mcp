import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: () => import('../views/LoginView.vue') },
    {
      path: '/',
      component: () => import('../layouts/AdminLayout.vue'),
      children: [
        { path: '', redirect: '/products' },
        { path: 'products', component: () => import('../views/ProductsView.vue') },
        { path: 'categories', component: () => import('../views/CategoriesView.vue') },
        { path: 'users', component: () => import('../views/UsersView.vue') },
        { path: 'orders', component: () => import('../views/OrdersView.vue') },
        { path: 'ledger', component: () => import('../views/LedgerView.vue') },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.path !== '/login' && !auth.isLoggedIn) {
    return '/login'
  }
})

export default router
