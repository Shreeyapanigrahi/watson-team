import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, ErrorBox } from '../ui'

const ROLE_TONE = { VIEWER: 'neutral', ANALYST: 'info', SPOC: 'warn', ADMIN: 'ok' }
const ROLE_CAN = {
  VIEWER: 'Upload documents. Reads PUBLIC and INTERNAL documents in their area and region.',
  ANALYST: 'Upload documents. Also reads RESTRICTED documents.',
  SPOC: 'Knowledge owner. Reviews and approves documents inside their therapeutic area.',
  ADMIN: 'Reviews everything, manages the lifecycle, runs the expiry scan, reads the audit log.',
}

export default function Login() {
  const { login } = useAuth()
  const [users, setUsers] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)

  useEffect(() => {
    api.users().then((r) => setUsers(r.users)).catch(setError)
  }, [])

  const pick = async (id) => {
    setBusy(id)
    setError(null)
    try {
      await login(id)
    } catch (e) {
      setError(e)
      setBusy(null)
    }
  }

  return (
    <div className="login">
      <div className="login-inner">
        <div>
          <h1>Watson</h1>
          <p className="muted" style={{ marginTop: 6 }}>
            Governed ingestion for pharma market research. Choose a test user to sign in. What you see and what you can do depends on that
            user's role, therapeutic area and region.
          </p>
        </div>
        <ErrorBox error={error} />
        {!users && !error && <div className="loading">Loading users...</div>}
        <div className="user-cards">
          {users?.map((u) => (
            <button key={u.user_id} className="user-card" onClick={() => pick(u.user_id)} disabled={busy !== null}>
              <div>
                <div className="name">{u.name}</div>
                <Badge tone={ROLE_TONE[u.role]}>{u.role}</Badge>
              </div>
              <div className="can">{ROLE_CAN[u.role]}</div>
              <div className="doc-sub">
                {u.therapeutic_areas.length ? u.therapeutic_areas.join(', ') : 'All therapeutic areas'} ·{' '}
                {u.geographies.length ? u.geographies.join(', ') : 'All regions'}
              </div>
              {busy === u.user_id && <div className="muted">Signing in...</div>}
            </button>
          ))}
        </div>
        <p className="doc-sub">Prototype sign-in: there are no passwords. See nfr_design.md for the production approach.</p>
      </div>
    </div>
  )
}
