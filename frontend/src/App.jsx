import { useState } from 'react'
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
import Account from './pages/Account'
import Chat from './pages/Chat'
import { ChatProvider } from './chat'
import { NewChat, RecentList } from './ChatSidebar'

const ROLE_TONE = { VIEWER: 'neutral', ANALYST: 'info', SPOC: 'warn', ADMIN: 'ok' }

function Shell({ children }) {
  const { user, logout, can } = useAuth()
  const viewer = user.role === 'VIEWER'            // the viewer's sidebar has no Layer 2 or About section
  const admin = user.role === 'ADMIN'              // nor does the admin's have the About section
  // The viewer's home page is the chat (Ask Watson); their Dashboard moves to /dashboard and Recent questions sit in the sidebar.
  const nav = [
    viewer ? { to: '/dashboard', label: 'Dashboard' } : { to: '/', label: 'Dashboard', end: true },
    { to: '/upload', label: 'Upload document' },
    { to: '/documents', label: 'Documents' },
    can('review') && { to: '/review', label: 'Review queue' },
    can('review') && { to: '/expiry', label: 'Expiry' },
    can('view_audit') && { to: '/audit', label: 'Audit log' },
    { to: '/catalog', label: 'Document catalog' },
    !viewer && { section: 'Ask' },
    !viewer && { to: '/ask', label: 'Ask Watson' },
    !viewer && { section: 'Knowledge' },
    !viewer && { to: '/knowledge', label: 'Knowledge space' },
    !viewer && { to: '/entities', label: 'Entities' },
    !viewer && { to: '/data-catalog', label: 'Data catalog' },
    !viewer && { to: '/ontology', label: 'Ontology' },
    can('validate_graph') && { to: '/graph-health', label: 'Graph health' },
    can('run_queries') && { to: '/queries', label: 'SPARQL queries' },
    !viewer && !admin && { section: 'About' },
    !viewer && !admin && { to: '/evidence', label: 'Design evidence' },
  ].filter(Boolean)
  const { pathname } = useLocation()
  const chatHome = viewer && pathname === '/'
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem('watson.sidebar') !== 'closed' } catch { return true }
  })
  const toggle = () => {
    setOpen((o) => {
      try { localStorage.setItem('watson.sidebar', o ? 'closed' : 'open') } catch { /* the choice just is not remembered */ }
      return !o
    })
  }
  return (
    <>
    <div className={`shell ${open ? '' : 'side-closed'}`}>
      {!open && (
        <button type="button" className="side-toggle side-toggle-open" onClick={toggle} aria-label="Open sidebar" title="Open sidebar">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" /></svg>
        </button>
      )}
      <aside className="sidebar" id="sidebar" hidden={!open}>
        <div className="brand">
          <span className="brand-mark">W</span>
          <div>
            <div className="brand-name">Watson</div>
            <div className="brand-sub">Ingestion · Semantic · Answers</div>
          </div>
          <button type="button" className="side-toggle side-toggle-close" onClick={toggle} aria-label="Close sidebar" aria-controls="sidebar" aria-expanded={open} title="Close sidebar">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" /></svg>
          </button>
        </div>
        {viewer && <NewChat />}
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
        {viewer && <RecentList />}
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
          <div className="row">
            <NavLink to="/account" className="btn btn-small btn-ghost">Account</NavLink>
            <button className="btn btn-small btn-ghost" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main className={`main ${chatHome ? 'main-chat' : ''}`}>{children}</main>
    </div>
    </>
  )
}

export default function App() {
  const { user, ready, can } = useAuth()
  const location = useLocation()
  if (!ready) return <div className="loading page-center">Loading...</div>
  if (!user) return <Login />
  const guard = (action, el) => (can(action) ? el : <Navigate to="/" replace state={{ from: location.pathname }} />)
  const viewer = user.role === 'VIEWER'
  const routes = (
    <Shell>
      <Routes>
        <Route path="/" element={viewer ? <Chat /> : <Dashboard />} />
        {viewer && <Route path="/dashboard" element={<Dashboard />} />}
        <Route path="/upload" element={<Upload />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/documents/:uri" element={<DocumentDetail />} />
        <Route path="/review" element={guard('review', <ReviewQueue />)} />
        <Route path="/expiry" element={guard('review', <Expiry />)} />
        <Route path="/audit" element={guard('view_audit', <Audit />)} />
        <Route path="/catalog" element={<Catalog />} />
        <Route path="/ask" element={viewer ? <Navigate to="/" replace /> : <Ask />} />
        <Route path="/knowledge" element={<KnowledgeSpace />} />
        <Route path="/entities" element={<Entities />} />
        <Route path="/entities/:id" element={<EntityDetail />} />
        <Route path="/data-catalog" element={<DataCatalog />} />
        <Route path="/ontology" element={<Ontology />} />
        <Route path="/graph-health" element={guard('validate_graph', <GraphHealth />)} />
        <Route path="/queries" element={guard('run_queries', <Queries />)} />
        <Route path="/evidence" element={<Evidence />} />
        <Route path="/account" element={<Account />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
  // The chat context sits above the whole shell so the sidebar (New chat, Recent) and the chat page share one conversation.
  return viewer ? <ChatProvider>{routes}</ChatProvider> : routes
}
