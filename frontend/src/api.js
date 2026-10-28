// Thin client for the Layer 1 API. Every call carries the session token.
let token = sessionStorage.getItem('watson_token')

export function setToken(t) {
  token = t
  if (t) sessionStorage.setItem('watson_token', t)
  else sessionStorage.removeItem('watson_token')
}
export const hasToken = () => Boolean(token)

let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}

async function request(path, { method = 'GET', body, form } = {}) {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  let payload
  if (form) payload = form
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch(`/api${path}`, { method, headers, body: payload })
  } catch {
    throw new Error('Cannot reach the Layer 1 server. Start it with: .venv\\Scripts\\python -m uvicorn layer1.api.main:app --port 8000')
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401) onUnauthorized()
    const detail = Array.isArray(data?.detail) ? data.detail.map((d) => d.msg).join('; ') : data?.detail
    const err = new Error(detail || res.statusText)
    err.status = res.status
    err.code = data?.code
    throw err
  }
  return data
}

const qs = (params = {}) => {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''))
  return s.toString() ? `?${s}` : ''
}
const enc = encodeURIComponent

export const api = {
  users: () => request('/users'),
  login: (user_id) => request('/login', { method: 'POST', body: { user_id } }),
  me: () => request('/me'),
  meta: () => request('/meta'),
  stats: () => request('/stats'),
  documents: (params) => request(`/documents${qs(params)}`),
  document: (uri) => request(`/documents/${enc(uri)}`),
  content: (uri) => request(`/documents/${enc(uri)}/content`),
  upload: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/documents', { method: 'POST', form })
  },
  review: (uri, body) => request(`/documents/${enc(uri)}/review`, { method: 'POST', body }),
  archive: (uri, notes) => request(`/documents/${enc(uri)}/archive`, { method: 'POST', body: { notes } }),
  reinstate: (uri, notes) => request(`/documents/${enc(uri)}/reinstate`, { method: 'POST', body: { notes } }),
  expiryUpcoming: (days) => request(`/expiry/upcoming${qs({ days })}`),
  expiryScan: () => request('/expiry/scan', { method: 'POST' }),
  audit: (params) => request(`/audit${qs(params)}`),
  catalog: () => request('/catalog'),
  chunking: () => request('/chunking-comparison'),
}
