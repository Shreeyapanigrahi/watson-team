import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, hasToken, setToken, setUnauthorizedHandler } from './api'

const Ctx = createContext(null)
export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [permissions, setPermissions] = useState([])
  const [meta, setMeta] = useState(null)
  const [ready, setReady] = useState(!hasToken())

  // clears the browser's session only; used when the server has already said the token is no good
  const clearSession = useCallback(() => {
    setToken(null)
    setUser(null)
    setPermissions([])
    setMeta(null)
  }, [])

  // sign out: ends the session on the server too (best effort), then clears it here
  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* the token may already have expired; signing out here is enough */
    }
    clearSession()
  }, [clearSession])

  const load = useCallback(async () => {
    const [me, m] = await Promise.all([api.me(), api.meta()])
    setUser(me.user)
    setPermissions(me.permissions)
    setMeta(m)
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(clearSession)
    if (hasToken()) load().catch(clearSession).finally(() => setReady(true))
  }, [load, clearSession])

  const login = useCallback(
    async (username, password) => {
      const res = await api.login(username, password)
      setToken(res.token)
      await load()
    },
    [load],
  )

  const can = useCallback((action) => permissions.includes(action), [permissions])

  return <Ctx.Provider value={{ user, meta, ready, login, logout, can }}>{children}</Ctx.Provider>
}
