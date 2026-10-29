import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth'
import { Badge } from './ui'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Upload from './pages/Upload'
import Documents from './pages/Documents'
import DocumentDetail from './pages/DocumentDetail'
import ReviewQueue from './pages/ReviewQueue'
import Expiry from './pages/Expiry'
import Audit from './pages/Audit'
import Catalog from './pages/Catalog'
import Evidence from './pages/Evidence'
import KnowledgeSpace from './pages/KnowledgeSpace'
import Entities from './pages/Entities'
import EntityDetail from './pages/EntityDetail'
import DataCatalog from './pages/DataCatalog'
import Ontology from './pages/Ontology'
import GraphHealth from './pages/GraphHealth'
import Queries from './pages/Queries'
import Ask from './pages/Ask'

const ROLE_TONE = { VIEWER: 'neutral', ANALYST: 'info', SPOC: 'warn', ADMIN: 'ok' }

function Shell({ children }) {
  const { user, logout, can } = useAuth()
  const nav = [
    { to: '/', label: 'Dashboard', end: true },
    { to: '/upload', label: 'Upload document' },
    { to: '/documents', label: 'Documents' },
    can('review') && { to: '/review', label: 'Review queue' },
    can('review') && { to: '/expiry', label: 'Expiry' },
    can('view_audit') && { to: '/audit', label: 'Audit log' },
    { to: '/catalog', label: 'Document catalog' },
    { section: 'Ask (Layer 3)' },
    { to: '/ask', label: 'Ask Watson' },
    { section: 'Knowledge (Layer 2)' },
    { to: '/knowledge', label: 'Knowledge space' },
    { to: '/entities', label: 'Entities' },
    { to: '/data-catalog', label: 'Data catalog' },
    { to: '/ontology', label: 'Ontology' },
    can('validate_graph') && { to: '/graph-health', label: 'Graph health' },
    can('run_queries') && { to: '/queries', label: 'SPARQL queries' },
    { section: 'About' },
    { to: '/evidence', label: 'Design evidence' },
  ].filter(Boolean)
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">W</span>
          <div>
            <div className="brand-name">Watson</div>
            <div className="brand-sub">Ingestion · Semantic · Answers</div>
          </div>
        </div>
        <nav>
          {nav.map((n) =>
            n.section ? (
              <div key={n.section} className="nav-section">{n.section}</div>
            ) : (
              <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-link ${isActive ? 'nav-active' : ''}`}>
                {n.label}
              </NavLink>
            ),
          )}
        </nav>
        <div className="whoami">
          <div className="whoami-name">{user.name}</div>
          <div className="whoami-meta">
            <Badge tone={ROLE_TONE[user.role]}>{user.role}</Badge>
          </div>
          <div className="whoami-scope">
            {user.therapeutic_areas.length ? user.therapeutic_areas.join(', ') : 'All therapeutic areas'}
            <br />
            {user.geographies.length ? user.geographies.join(', ') : 'All regions'}
          </div>
          <button className="btn btn-small btn-ghost" onClick={logout}>
            Switch user
          </button>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  )
}

export default function App() {
  const { user, ready, can } = useAuth()
  const location = useLocation()
  if (!ready) return <div className="loading page-center">Loading...</div>
  if (!user) return <Login />
  const guard = (action, el) => (can(action) ? el : <Navigate to="/" replace state={{ from: location.pathname }} />)
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/documents/:uri" element={<DocumentDetail />} />
        <Route path="/review" element={guard('review', <ReviewQueue />)} />
        <Route path="/expiry" element={guard('review', <Expiry />)} />
        <Route path="/audit" element={guard('view_audit', <Audit />)} />
        <Route path="/catalog" element={<Catalog />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="/knowledge" element={<KnowledgeSpace />} />
        <Route path="/entities" element={<Entities />} />
        <Route path="/entities/:id" element={<EntityDetail />} />
        <Route path="/data-catalog" element={<DataCatalog />} />
        <Route path="/ontology" element={<Ontology />} />
        <Route path="/graph-health" element={guard('validate_graph', <GraphHealth />)} />
        <Route path="/queries" element={guard('run_queries', <Queries />)} />
        <Route path="/evidence" element={<Evidence />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}
