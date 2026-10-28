import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, hasToken, setToken, setUnauthorizedHandler } from './api'

const Ctx = createContext(null)
export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [permissions, setPermissions] = useState([])
  const [meta, setMeta] = useState(null)
  const [ready, setReady] = useState(!hasToken())

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
    setPermissions([])
    setMeta(null)
  }, [])

  const load = useCallback(async () => {
    const [me, m] = await Promise.all([api.me(), api.meta()])
    setUser(me.user)
    setPermissions(me.permissions)
    setMeta(m)
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(logout)
    if (hasToken()) load().catch(logout).finally(() => setReady(true))
  }, [load, logout])

  const login = useCallback(
    async (userId) => {
      const res = await api.login(userId)
      setToken(res.token)
      await load()
    },
    [load],
  )

  const can = useCallback((action) => permissions.includes(action), [permissions])

  return <Ctx.Provider value={{ user, meta, ready, login, logout, can }}>{children}</Ctx.Provider>
}
