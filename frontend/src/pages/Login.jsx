import { useRef, useState } from 'react'
import { useAuth } from '../auth'
import { ErrorBox } from '../ui'
import LoginDoctors from './LoginDoctors'

const Icon = ({ d }) => (
  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
)

export default function Login() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const passwordBox = useRef(null)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError(err)
      setPassword('')
      setBusy(false)
      passwordBox.current?.focus()
    }
  }

  return (
    <div className="lg">
      <section className="lg-panel">
        <div className="lg-brand">
          <span className="brand-mark">W</span>
          <span className="lg-brand-name">Watson</span>
        </div>
        <div>
          <h1 className="lg-headline">Market research you can trace back to its source.</h1>
          <p className="lg-lede">Ask questions about your approved pharma research. Every statement in an answer names the document it came from.</p>
          <p className="lg-foot">You see only the documents your role allows.</p>
        </div>
        <LoginDoctors />
      </section>
      <main className="lg-form-col">
      <form className="lg-form" onSubmit={submit}>
        <h2 className="lg-form-title">Sign in</h2>

        <ErrorBox error={error} />

        <div className="lg-field">
          <label htmlFor="username">Username</label>
          <div className="lg-input">
            <input id="username" name="username" type="text" autoComplete="username" autoFocus required value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
        </div>
        <div className="lg-field">
          <label htmlFor="password">Password</label>
          <div className="lg-input">
            <input id="password" name="password" ref={passwordBox} type={reveal ? 'text' : 'password'} autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="lg-reveal" onClick={() => setReveal((v) => !v)} aria-label={reveal ? 'Hide password' : 'Show password'} aria-pressed={reveal}>
              <Icon d={reveal ? 'M3 3l18 18M10.6 6.2A10 10 0 0 1 22 12a14 14 0 0 1-3.2 4M6.5 6.9A14 14 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 4-.9' : 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'} />
            </button>
          </div>
        </div>

        <button type="submit" className="btn btn-primary lg-submit" disabled={busy || !username.trim() || !password}>
          {busy ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
      </main>
    </div>
  )
}
