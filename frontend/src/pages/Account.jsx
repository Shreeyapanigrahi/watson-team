import { useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, ErrorBox } from '../ui'

const MIN = 10

export default function Account() {
  const { user } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  const problem = next && next.length < MIN ? `At least ${MIN} characters (${next.length} so far).` : again && again !== next ? 'The two new passwords differ.' : ''

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setDone(false)
    try {
      await api.changePassword(current, next)
      setDone(true)
      setCurrent('')
      setNext('')
      setAgain('')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Account</h1>
          <p>
            Signed in as <strong>{user.name}</strong> <Badge tone="info">{user.role}</Badge>. Changing your password signs you out of any other browser
            where you are signed in.
          </p>
        </div>
      </div>
      <form className="card stack" onSubmit={submit} style={{ maxWidth: 460 }}>
        <h3>Change password</h3>
        <ErrorBox error={error} />
        {done && <div className="alert alert-ok" role="status">Password changed. Your other sessions were signed out.</div>}
        <div>
          <label htmlFor="cur" style={{ fontWeight: 600 }}>Current password</label>
          <input id="cur" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div>
          <label htmlFor="new" style={{ fontWeight: 600 }}>New password</label>
          <input id="new" type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
        </div>
        <div>
          <label htmlFor="again" style={{ fontWeight: 600 }}>New password again</label>
          <input id="again" type="password" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)} />
        </div>
        {problem && <div className="doc-sub" role="alert">{problem}</div>}
        <button type="submit" className="btn btn-primary" disabled={busy || !current || next.length < MIN || next !== again}>
          {busy ? 'Saving...' : 'Change password'}
        </button>
      </form>
    </div>
  )
}
