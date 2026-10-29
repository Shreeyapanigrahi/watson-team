# Layer 2: Semantic Foundation, architecture

Status: **built and tested** (133 Layer 2 tests, 182 with Layer 1; SHACL conforms; UI built but not visually checked by us, see `project_status.md`).

The rule that shapes everything: **the data catalog JSON owns names, synonyms, hierarchy and mappings; `pharma.ttl` owns structure.**
The graph, the vector index and the caches are derived, and `python -m layer2.backfill --reset` rebuilds them.

## 1. From the three model files to the catalog

```mermaid
flowchart LR
    S1["architecture/model.json<br/>(base)"] --> B["catalog/build_catalog.py"]
    S2["architecture/*_mvm.csv<br/>(cross-check only)"] --> B
    S3["architecture/*_rdf_*.ttl<br/>(class and property names)"] --> B
    E["watson_extension.json<br/>our reference rows, grounding list"] --> B
    AL["alignments.json<br/>DOID / MeSH / ChEBI IDs looked up"] --> B
    B -->|"fails if CSV disagrees,<br/>or a reference is missing"| CAT["pharma_data_catalog.json<br/>15 domains + pmr extension,<br/>213 tables, 8,874 columns"]
```

The three source files are never modified. The catalog version is a fingerprint of its content.

## 2. Enrichment of one approved document

```mermaid
flowchart TB
    L1["Layer 1: reviewer approves<br/>document becomes ACTIVE"] -->|"state hook"| T{"taxonomy check<br/>(no side effects)"}
    T -- "unknown value" --> F1["ENRICHMENT_FAILED<br/>nothing written"]
    T -- "ok" --> N["entity_extractor.py<br/>EntityRuler built from the catalog"]
    N --> R["entity_resolver.py<br/>exact, fuzzy (> 0.85), or provisional"]
    R --> SE["sentiment_extractor.py<br/>score, brand, indication, region, date"]
    R --> EM["embedder.py<br/>MiniLM ONNX, 384 dims"]
    EM --> V["vector_index.py<br/>FAISS flat, stable ids"]
    R --> TW["triple_writer.py<br/>idempotent per document"]
    SE --> TW
    TW --> KG[("kg.py<br/>rdflib graph<br/>pharma_kg.ttl")]
    V --> KG
    KG --> SH{"shacl_validator.py<br/>15 shapes + index binding"}
```

Computed first, written together at the end. A failure logs `ENRICHMENT_FAILED`, rolls back the vectors, and leaves the document ACTIVE.
When a document leaves ACTIVE (archived, expired, reinstated for review) the same hook removes its vectors and recomputes the links between brands.

## 3. Reading: who sees what

```mermaid
flowchart LR
    U["User (role + TA + region)"] --> A["graph_view.visible_records()<br/>ACTIVE and ABAC allows"]
    A --> CH["readable chunks"]
    CH --> VS["vector search over<br/>readable vectors only"]
    CH --> GV["graph, entities, spaces,<br/>sentiment, co-mentions<br/>(recomputed per user)"]
    VS --> TEL["telemetry.py<br/>RetrievalEvent"]
    TEL --> Q10["SPARQL Q10<br/>role vs sensitivity audit"]
```

## 4. Components and screens

```mermaid
flowchart TB
    subgraph FE["frontend/ (React 18 + Vite + Cytoscape)"]
        P["Knowledge space, Entities, Entity detail,<br/>Data catalog, Ontology,<br/>Graph health (SPOC, ADMIN), SPARQL queries (ADMIN),<br/>Document detail: Knowledge tab"]
    end
    subgraph API["layer1/api/main.py mounts layer2/api/router.py at /api/l2"]
        EP["catalog, ontology, entities, graph, spaces,<br/>enrichment, provisional, taxonomy,<br/>validate, queries, search"]
    end
    subgraph L2["layer2/"]
        CV["catalog_view, ontology_view, graph_view"]
        EN["enrichment_pipeline + extractor, resolver,<br/>sentiment, embedder, triple_writer"]
        ST["kg.py, vector_index.py, telemetry.py"]
        AD["catalog_admin.py<br/>approve alias / new / reject"]
        SP["sparql_runner.py, queries.sparql"]
    end
    FE --> API --> L2
```

| Screen | Route | Who |
|---|---|---|
| Knowledge space (graph, spaces) | `/knowledge` | all |
| Entities and entity detail (grounding, relations, sentiment) | `/entities`, `/entities/:id` | all (document counts follow access) |
| Data catalog (overview, browse tables, mappings into Watson) | `/data-catalog` | all |
| Ontology (brief minimums, classes, properties, alignments) | `/ontology` | all |
| Graph health (SHACL, enrichment, unrecognised names, taxonomies) | `/graph-health` | SPOC, ADMIN |
| SPARQL queries Q1-Q11 and custom | `/queries` | ADMIN |
| Document detail, Knowledge tab | `/documents/:uri` | per document access |

## 5. Where things live

| What | Where |
|---|---|
| Catalog | `layer2/catalog/pharma_data_catalog.json` |
| Ontology and shapes | `layer2/ontology/pharma.ttl`, `shapes.ttl` |
| Derived stores | `layer2/stores/` (graph, FAISS index, chunk map, enrichment status, entity mentions, provisional names, telemetry) |
| Saved query results | `results/query_results/` (Q1-Q11 as JSON and Markdown) |
