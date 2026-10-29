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

// Layer 3 streams one JSON object per line while the pipeline runs. onEvent is called for each as it arrives.
async function streamAsk(question, mode, onEvent, signal) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  let res
  try {
    res = await fetch('/api/l3/ask', { method: 'POST', headers, body: JSON.stringify({ question, mode }), signal })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new Error('Cannot reach the server. Start it with: .venv\\Scripts\\python -m uvicorn layer1.api.main:app --port 8000')
  }
  if (!res.ok) {
    if (res.status === 401) onUnauthorized()
    const data = await res.json().catch(() => null)
    const detail = Array.isArray(data?.detail) ? data.detail.map((d) => d.msg).join('; ') : data?.detail
    throw new Error(detail || res.statusText)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let nl
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim()
      buf = buf.slice(nl + 1)
      if (line) onEvent(JSON.parse(line))
    }
  }
  if (buf.trim()) onEvent(JSON.parse(buf))
}

export const api = {
  l3Ask: streamAsk,
  l3Status: () => request('/l3/status'),
  l3History: () => request('/l3/history'),
  l3DeleteRun: (id) => request(`/l3/history/${enc(id)}`, { method: 'DELETE' }),
  l3ClearHistory: () => request('/l3/history', { method: 'DELETE' }),
  login: (username, password) => request('/login', { method: 'POST', body: { username, password } }),
  logout: () => request('/logout', { method: 'POST' }),
  changePassword: (current_password, new_password) => request('/me/password', { method: 'POST', body: { current_password, new_password } }),
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

  // ---- Layer 2: semantic foundation (everything is filtered by the server to what this user may read)
  l2Status: () => request('/l2/status'),
  l2CatalogSummary: () => request('/l2/catalog/summary'),
  l2CatalogTables: (params) => request(`/l2/catalog/tables${qs(params)}`),
  l2CatalogTable: (fqn) => request(`/l2/catalog/table${qs({ fqn })}`),
  l2CatalogSearch: (q, kind) => request(`/l2/catalog/search${qs({ q, kind })}`),
  l2Mappings: () => request('/l2/catalog/mappings'),
  l2Ontology: () => request('/l2/ontology'),
  l2Entities: (params) => request(`/l2/entities${qs(params)}`),
  l2Entity: (id) => request(`/l2/entities/${enc(id)}`),
  l2Graph: (params) => request(`/l2/graph${qs(params)}`),
  l2GraphFilters: (params) => request(`/l2/graph/filters${qs(params)}`),
  l2DocumentFacts: (uri) => request(`/l2/documents/${enc(uri)}/facts`),
  l2Spaces: () => request('/l2/spaces'),
  l2Knowledge: (uri) => request(`/l2/documents/${enc(uri)}/knowledge`),
  l2Enrich: (uri) => request(`/l2/documents/${enc(uri)}/enrich`, { method: 'POST' }),
  l2Enrichment: () => request('/l2/enrichment'),
  l2Provisional: (status) => request(`/l2/provisional${qs({ status })}`),
  l2Approve: (id, body) => request(`/l2/provisional/${enc(id)}/approve`, { method: 'POST', body }),
  l2Reject: (id, note) => request(`/l2/provisional/${enc(id)}/reject`, { method: 'POST', body: { note } }),
  l2Taxonomy: () => request('/l2/taxonomy'),
  l2AddTerm: (name, label) => request(`/l2/taxonomy/${enc(name)}/terms`, { method: 'POST', body: { label } }),
  l2Validate: () => request('/l2/validate', { method: 'POST' }),
  l2ValidateLast: () => request('/l2/validate/last'),
  l2Queries: () => request('/l2/queries'),
  l2RunQuery: (id, params, save) => request(`/l2/queries/${enc(id)}/run`, { method: 'POST', body: { params, save: Boolean(save) } }),
  l2CustomQuery: (sparql) => request('/l2/queries/custom', { method: 'POST', body: { sparql } }),
}
