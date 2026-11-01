import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Badge, Empty, ErrorBox, KV, Loading, Notice, Tabs, useAsync } from '../ui'

const STATUS_TONE = { grounded: 'ok', partial: 'warn', extension: 'info' }
const STATUS_HELP = {
  grounded: 'A table or column in the catalog holds this concept',
  partial: 'The catalog holds part of it',
  extension: 'The catalog has no counterpart; Watson added it',
}
const short = (fqn) => (fqn || '').split('.').slice(1).join('.')
const n = (v) => (v ?? 0).toLocaleString()

export default function DataCatalog() {
  const [tab, setTab] = useState('overview')
  const [open, setOpen] = useState(null) // fqn of the table being viewed
  const summary = useAsync(() => api.l2CatalogSummary(), [])

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Data catalog</h1>
          <p>
            The catalog is the single source of truth for what Watson's entities are called and where they live. It was built from the three model
            files in the architecture folder; nothing was fetched from the web. Every ontology term and every entity can be traced to a table here.
          </p>
        </div>
      </div>
      <Tabs
        tabs={[{ id: 'overview', label: 'Overview' }, { id: 'browse', label: 'Browse tables' }, { id: 'mappings', label: 'Mappings into Watson' }]}
        value={tab}
        onChange={setTab}
      />
      <ErrorBox error={summary.error} onRetry={summary.reload} />
      {summary.loading && !summary.data && <Loading />}
      {summary.data && tab === 'overview' && <Overview s={summary.data} onBrowse={(d) => { setTab('browse'); setOpen({ domain: d }) }} />}
      {summary.data && tab === 'browse' && <Browse domains={summary.data.domains} start={open} />}
      {tab === 'mappings' && <Mappings />}
    </div>
  )
}

function Overview({ s, onBrowse }) {
  const c = s.counts
  const rec = s.reconciliation || {}
  return (
    <div className="stack">
      <div className="tiles">
        <div className="tile"><div className="tile-value">{n(c.domains + 1)}</div><div className="tile-label">domains</div><div className="doc-sub">{c.domains} supplied + 1 Watson</div></div>
        <div className="tile"><div className="tile-value">{n(c.tables + (s.extension?.extension_tables || 0))}</div><div className="tile-label">tables</div></div>
        <div className="tile"><div className="tile-value">{n(c.columns + (s.extension?.extension_columns || 0))}</div><div className="tile-label">columns</div></div>
        <div className="tile"><div className="tile-value">{n(c.foreign_keys)}</div><div className="tile-label">foreign keys</div><div className="doc-sub">{n(c.foreign_keys_llm_generated)} AI-generated</div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <h3>Where it came from</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>File</th><th>Role</th><th>SHA-256 (start)</th></tr></thead>
              <tbody>
                {s.sources.map((f) => (
                  <tr key={f.file}><td className="mono">{f.file}</td><td>{f.role}</td><td className="mono" title={f.sha256}>{f.sha256.slice(0, 12)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <KV rows={[['Catalog version', <span className="mono">{s.version}</span>, 'A fingerprint of the content; it changes whenever the catalog does'], ['Model', `${s.model} ${s.model_version || ''}`]]} />
        </div>
        <div className="card">
          <h3>How the three files agree</h3>
          <ul className="plain">
            {rec.csv_vs_model_json && <li><strong>CSV against JSON:</strong> {n(rec.csv_vs_model_json.matched_to_model_json)} of {n(rec.csv_vs_model_json.csv_rows)} rows match, {rec.csv_vs_model_json.unexplained_differences} unexplained differences. {rec.csv_vs_model_json.conclusion}</li>}
            {rec.ttl_vs_model_json && <li><strong>RDF against JSON:</strong> {n(rec.ttl_vs_model_json.classes_in_ttl)} classes in the RDF file, {n(rec.ttl_vs_model_json.classes_expected_from_model_json)} expected from the JSON; {rec.ttl_vs_model_json.tables_without_class.length} tables without a class.</li>}
          </ul>
        </div>
      </div>

      <div className="card">
        <h3>Domains</h3>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Domain</th><th>Division</th><th className="num">Tables</th><th className="num">Columns</th><th className="num">Tables Watson uses</th><th>Origin</th></tr></thead>
            <tbody>
              {s.domains.map((d) => (
                <tr key={d.name} className="clickable" onClick={() => onBrowse(d.name)}>
                  <td><strong>{d.name}</strong><div className="doc-sub">{d.description}</div></td>
                  <td>{d.division}</td>
                  <td className="num">{d.tables}</td>
                  <td className="num">{n(d.columns)}</td>
                  <td className="num">{d.tables_used_by_watson}</td>
                  <td><Badge tone={d.origin === 'extension' ? 'warn' : 'info'}>{d.origin === 'extension' ? 'Watson extension' : 'supplied model'}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Browse({ domains, start }) {
  const [domain, setDomain] = useState(start?.domain || '')
  const [q, setQ] = useState('')
  const [watsonOnly, setWatsonOnly] = useState(false)
  const [fqn, setFqn] = useState(null)
  const list = useAsync(() => api.l2CatalogTables({ domain, q, watson_only: watsonOnly || undefined }), [domain, q, watsonOnly])
  const hits = useAsync(() => (q.length >= 3 ? api.l2CatalogSearch(q) : Promise.resolve({ hits: [] })), [q])

  if (fqn) return <TableDetail fqn={fqn} onBack={() => setFqn(null)} onOpen={setFqn} />
  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 12 }}>
        <label htmlFor="dom" style={{ fontWeight: 600 }}>Domain</label>
        <select id="dom" value={domain} onChange={(e) => setDomain(e.target.value)} style={{ width: 'auto' }}>
          <option value="">All domains</option>
          {domains.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
        </select>
        <label htmlFor="tq" style={{ fontWeight: 600 }}>Search</label>
        <input id="tq" type="search" value={q} placeholder="Table name or words from its description" onChange={(e) => setQ(e.target.value)} style={{ width: 280 }} />
        <label className="check">
          <input type="checkbox" checked={watsonOnly} onChange={(e) => setWatsonOnly(e.target.checked)} /> Only tables Watson uses
        </label>
      </div>
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loading />}
      {hits.data?.hits.length > 0 && (
        <div className="alert alert-info" style={{ marginBottom: 12 }}>
          <strong>Best matches for "{q}" (tables and columns)</strong>
          <ul className="plain">
            {hits.data.hits.slice(0, 8).map((h) => (
              <li key={h.fqn}>
                <button className="btn btn-small" style={{ padding: '0 6px' }} onClick={() => setFqn(h.kind === 'table' ? h.fqn : h.fqn.split('.').slice(0, 3).join('.'))}>{short(h.fqn)}</button>{' '}
                <span className="muted">{h.kind}{h.glossary_term ? ` · ${h.glossary_term}` : ''}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {list.data && list.data.tables.length === 0 && <Empty title="No table matches">Try fewer words, or clear the filters.</Empty>}
      {list.data && list.data.tables.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Table</th><th>Domain</th><th className="num">Columns</th><th>Primary key</th><th className="num">Watson uses</th></tr></thead>
              <tbody>
                {list.data.tables.map((t) => (
                  <tr key={t.fqn} className="clickable" onClick={() => setFqn(t.fqn)}>
                    <td><strong>{t.name}</strong><div className="doc-sub">{(t.description || '').slice(0, 140)}</div></td>
                    <td>{t.domain}{t.origin === 'extension' && <> <Badge tone="warn">Watson</Badge></>}</td>
                    <td className="num">{t.columns}</td>
                    <td className="mono">{t.primary_key}</td>
                    <td className="num">{t.watson_usage || <span className="muted">0</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="doc-sub" style={{ marginTop: 8 }}>
            {n(list.data.count)} tables{list.data.truncated ? ' (first 400 shown; narrow the search to see the rest)' : ''}
          </p>
        </>
      )}
    </div>
  )
}

function TableDetail({ fqn, onBack, onOpen }) {
  const t = useAsync(() => api.l2CatalogTable(fqn), [fqn])
  if (t.loading && !t.data) return <Loading />
  if (t.error) return <div className="stack"><button className="btn" onClick={onBack}>Back to tables</button><ErrorBox error={t.error} onRetry={t.reload} /></div>
  const d = t.data
  return (
    <div className="stack">
      <div><button className="btn" onClick={onBack}>Back to tables</button></div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>{d.name}</h2>
        <p className="doc-sub mono">{d.fqn}</p>
        <p>{d.description}</p>
        <KV rows={[['Primary key', <span className="mono">{d.primary_key}</span>], ['RDF class (supplied file)', d.rdf_class ? <span className="mono">{d.rdf_class}</span> : null],
          ['Origin', d.origin === 'extension' ? 'Watson extension' : 'supplied model']]} />
        {d.watson_usage.length > 0 && (
          <>
            <h3>How Watson uses this table</h3>
            <ul className="plain">{d.watson_usage.map((u) => <li key={u.type + u.name}><Badge tone={STATUS_TONE[u.status]}>{u.type}</Badge> {u.name} <span className="muted">({u.kind})</span></li>)}</ul>
          </>
        )}
      </div>

      <div className="grid-2">
        <FkList title="References (foreign keys leaving this table)" rows={d.references} side="to" onOpen={onOpen} />
        <FkList title="Referenced by (foreign keys pointing here)" rows={d.referenced_by} side="from" onOpen={onOpen} />
      </div>

      <div className="card">
        <h3>Columns ({d.columns.length})</h3>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Column</th><th>Type</th><th>Meaning</th><th>Classification</th><th>Watson</th></tr></thead>
            <tbody>
              {d.columns.map((c) => (
                <tr key={c.fqn}>
                  <td className="mono">{c.name}{c.is_primary_key && <> <Badge tone="info">PK</Badge></>}{c.foreign_key_to && <> <Badge tone="neutral" title={c.foreign_key_to}>FK</Badge>{c.fk_llm_generated && <Badge tone="warn" title="The supplied model marks this link as generated by an AI, so it was not confirmed by a person">AI link</Badge>}</>}</td>
                  <td className="mono">{c.type}</td>
                  <td>{c.glossary_term && <strong>{c.glossary_term}. </strong>}{c.description}</td>
                  <td>
                    {c.classification_conflict ? <Badge tone="bad" title="Two tags disagree, so none is applied">conflict</Badge>
                      : c.classification ? <Badge tone="info">{c.classification}</Badge> : <span className="muted">untagged</span>}
                    {c.pii_tags.length > 0 && <> <Badge tone="bad">PII</Badge></>}
                  </td>
                  <td>{c.watson_usage.map((u) => <div key={u.type + u.name}>{u.name}</div>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function FkList({ title, rows, side, onOpen }) {
  const other = side === 'to' ? 'to_table' : 'from_table'
  return (
    <div className="card">
      <h3>{title} ({rows.length})</h3>
      {rows.length === 0 ? <p className="muted">None.</p> : (
        <ul className="plain" style={{ maxHeight: 260, overflow: 'auto' }}>
          {rows.map((r) => (
            <li key={r.from_column + r.to_column}>
              <button className="btn btn-small" style={{ padding: '0 6px' }} onClick={() => onOpen(r[other])}>{short(r[other])}</button>{' '}
              <span className="muted mono">{(side === 'to' ? r.from_column : r.to_column).split('.').pop()}</span>{' '}
              {r.llm_generated && <Badge tone="warn" title="Marked in the supplied model as generated by an AI">AI link</Badge>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Mappings() {
  const m = useAsync(() => api.l2Mappings(), [])
  const [view, setView] = useState('terms')
  if (m.loading && !m.data) return <Loading />
  if (m.error) return <ErrorBox error={m.error} onRetry={m.reload} />
  const d = m.data
  const c = d.ontology_term_counts
  return (
    <div className="stack">
      <Notice tone="info">{d.note}</Notice>
      <Tabs
        tabs={[{ id: 'terms', label: 'Ontology terms', count: d.ontology_terms.length }, { id: 'entities', label: 'Entity values' },
          { id: 'relations', label: 'Relations', count: d.relations.length }, { id: 'align', label: 'External IDs' }]}
        value={view}
        onChange={setView}
      />
      {view === 'terms' && (
        <div className="card">
          <p><Badge tone="ok">{c.grounded} grounded</Badge> <Badge tone="warn">{c.partial} partial</Badge> <Badge tone="info">{c.extension} extension</Badge>{' '}
            <span className="muted">{Object.values(STATUS_HELP).join('; ')}.</span></p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Ontology term</th><th>Kind</th><th>Status</th><th>Catalog table or column</th><th>Note</th></tr></thead>
              <tbody>
                {d.ontology_terms.map((t) => (
                  <tr key={t.term}>
                    <td><strong>{t.term}</strong></td>
                    <td>{t.kind}</td>
                    <td><Badge tone={STATUS_TONE[t.status]} title={STATUS_HELP[t.status]}>{t.status}</Badge></td>
                    <td className="mono">{t.catalog.length ? t.catalog.map((r) => <div key={r.fqn}>{short(r.fqn) || r.fqn}{!r.exists && <Badge tone="bad"> missing</Badge>}</div>) : <span className="muted">none</span>}</td>
                    <td>{t.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {view === 'entities' && (
        <div className="card">
          <p className="muted">For each kind of entity, the catalog columns its values were read from. Open <Link to="/entities">Entities</Link> to see one entity's own values.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Entity kind</th><th className="num">Entities</th><th>Catalog column</th><th className="num">Entities using it</th></tr></thead>
              <tbody>
                {d.entity_mappings.flatMap((k) => (k.columns.length ? k.columns : [null]).map((col, i) => (
                  <tr key={k.kind + (col?.fqn || i)}>
                    <td>{i === 0 ? <strong>{k.kind}</strong> : ''}</td>
                    <td className="num">{i === 0 ? k.entities : ''}</td>
                    <td className="mono">{col ? short(col.fqn) : <span className="muted">none</span>}</td>
                    <td className="num">{col?.entities}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {view === 'relations' && (
        <div className="card">
          <div className="table-wrap">
            <table>
              <thead><tr><th>Relationship</th><th>Backed by catalog column</th><th>Points to</th><th>Link quality</th></tr></thead>
              <tbody>
                {d.relations.map((r) => (
                  <tr key={r.relation}>
                    <td><strong>{r.relation}</strong></td>
                    <td className="mono">{r.column ? short(r.column) : <span className="muted">{r.note}</span>}</td>
                    <td className="mono">{r.target_table ? short(r.target_table) : ''}</td>
                    <td>{r.column ? <Badge tone={r.ai_generated_link ? 'warn' : 'ok'}>{r.ai_generated_link ? 'AI-generated link' : 'confirmed link'}</Badge> : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {view === 'align' && (
        <div className="grid-2">
          {[['DOID', 'Indications and the Disease Ontology (exact matches)'], ['MeSH', 'Therapeutic areas and MeSH headings (close matches)']].map(([key, title]) => (
            <div className="card" key={key}>
              <h3>{title}</h3>
              <p className="muted">{d.alignments[key].filter((a) => a.external_id).length} of {d.alignments[key].length} have an ID. The rest are left blank rather than guessed.</p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Entity</th><th>{key}</th><th>Their label</th></tr></thead>
                  <tbody>
                    {d.alignments[key].map((a) => (
                      <tr key={a.id}>
                        <td><Link to={`/entities/${encodeURIComponent(a.id)}`}>{a.entity}</Link></td>
                        <td className="mono">{a.external_id || <span className="muted">none</span>}</td>
                        <td>{a.external_label}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
