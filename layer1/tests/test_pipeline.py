"""Ingestion checks end to end: dedup, PII, metadata, lifecycle, ABAC, expiry, quality, audit."""
from datetime import datetime, timedelta, timezone

import pytest

from layer1 import abac_engine as abac
from layer1 import audit_log, expiry_manager, lifecycle_manager as lm, pii_detector, quality_scorer
from layer1.errors import (LifecycleViolationError, MetadataIncompleteError, PIIBlockedError,
                           WatsonAccessDeniedError)


def approve(uri, user="u_admin", **kw):
    args = dict(sensitivity_level="INTERNAL", notes="ok")
    args.update(kw)
    return lm.review(uri, True, user, **args)


# ------------------------------------------------------------------ dedup
def test_exact_duplicate_is_rejected_with_original_uri(env, corpus):
    first = lm.ingest(corpus / "study_012.docx")
    dup = lm.ingest(corpus / "study_012_copy.docx")
    assert dup["state"] == "REJECTED"
    assert dup["assessment"]["dedup"]["verdict"] == "exact_duplicate"
    assert dup["assessment"]["dedup"]["original_uri"] == first["document_uri"]
    hit = audit_log.read_events(dup["document_uri"], "DEDUP_HIT")[0]["details"]
    assert hit["original_uri"] == first["document_uri"]


def test_near_duplicate_is_held_for_review_with_ratio(env, corpus):
    first = lm.ingest(corpus / "study_007.docx")
    near = lm.ingest(corpus / "study_007_rev.docx")
    d = near["assessment"]["dedup"]
    assert near["state"] == "UNDER_REVIEW" and d["verdict"] == "near_duplicate"
    assert d["original_uri"] == first["document_uri"] and 0.85 < d["similarity"] < 1.0
    assert any("near duplicate - please verify" in n for n in near["reviewer_notes"])


def test_new_version_flagged_only_against_an_active_document(env, corpus):
    lm.ingest(corpus / "study_013.pptx")                    # still UNDER_REVIEW, not ACTIVE
    update = lm.ingest(corpus / "study_013_update.pdf")
    assert update["assessment"]["dedup"]["verdict"] == "unique"


def test_unrelated_documents_are_not_compared(env, corpus):
    lm.ingest(corpus / "study_001.pdf")
    other = lm.ingest(corpus / "study_028.pdf")
    assert other["assessment"]["dedup"]["verdict"] == "unique"


# ------------------------------------------------------------------ PII
def test_planted_pii_is_flagged_with_all_identifier_types(env, corpus):
    rec = lm.ingest(corpus / "study_036.docx")
    pii = rec["assessment"]["pii"]
    assert pii["pii_detected"] and {"EMAIL", "DATE_OF_BIRTH", "PHONE", "PERSON"} <= set(pii["pii_types"])
    assert rec["state"] == "UNDER_REVIEW"                   # flagged, not auto-decided
    assert audit_log.read_events(rec["document_uri"], "PII_FLAG")


def test_named_kol_in_attributed_quote_is_not_pii():
    res = pii_detector.detect_chunk("Dr. Elena Rossi, a leading oncologist in Italy, noted that tolerability matters most.")
    assert not res["pii_detected"]


def test_patient_name_with_diagnosis_is_pii():
    res = pii_detector.detect_chunk("The patient Maria Keller was diagnosed with melanoma last spring.")
    assert res["pii_detected"] and "PERSON" in res["pii_types"]


def test_no_false_positives_across_the_rest_of_the_corpus(env, corpus, manifest):
    flagged = []
    for name in manifest:
        if name == "study_036.docx":
            continue
        rec = lm.ingest(corpus / name)
        if rec["assessment"]["pii"]["pii_detected"]:
            flagged.append(name)
    assert flagged == []


def test_pii_blocks_approval_until_explicit_override_with_reason(env, corpus):
    rec = lm.ingest(corpus / "study_036.docx")
    uri = rec["document_uri"]
    with pytest.raises(PIIBlockedError):
        approve(uri, sensitivity_level="RESTRICTED")
    with pytest.raises(PIIBlockedError):
        approve(uri, sensitivity_level="RESTRICTED", pii_override=True)       # no reason given
    out = approve(uri, sensitivity_level="RESTRICTED", pii_override=True, pii_override_reason="Public KOL, consent on file")
    assert out["state"] == "ACTIVE" and out["review"]["pii_override"] is True


# ------------------------------------------------------------------ lifecycle
def test_full_happy_path_writes_access_expiry_quality_and_audit(env, corpus):
    rec = lm.ingest(corpus / "study_001.pdf")
    assert rec["state"] == "UNDER_REVIEW" and rec["access"] is None
    out = lm.review(rec["document_uri"], True, "u_admin", sensitivity_level="RESTRICTED",
                    allowed_therapeutic_areas=["Oncology"], allowed_geographies=["EU"], notes="Verified. No PII.")
    assert out["state"] == "ACTIVE"
    assert out["access"] == {"sensitivity_level": "RESTRICTED", "allowed_therapeutic_areas": ["Oncology"],
                             "allowed_geographies": ["EU"]}
    assert out["expiry_date"] and 0 < out["quality"]["quality_score"] <= 1
    types = [e["event_type"] for e in audit_log.read_events(rec["document_uri"])]
    assert types[0] == "INGEST" and types.count("STATE_TRANSITION") == 2


def test_default_access_scope_is_the_documents_own_area_and_region(env, corpus):
    rec = lm.ingest(corpus / "study_001.pdf")
    out = approve(rec["document_uri"])
    assert out["access"]["allowed_therapeutic_areas"] == [out["metadata"]["therapeutic_area"]]
    assert out["access"]["allowed_geographies"] == [out["metadata"]["geography"]]


def test_illegal_transitions_raise_and_are_audited(env, corpus):
    rec = lm.ingest(corpus / "study_012.docx")
    uri = rec["document_uri"]
    with pytest.raises(LifecycleViolationError):
        lm.transition(uri, "ARCHIVED", "u_admin")                      # UNDER_REVIEW -> ARCHIVED
    rejected = lm.review(uri, False, "u_admin", reason="test")
    with pytest.raises(LifecycleViolationError):
        lm.transition(uri, "ACTIVE", "u_admin")                        # REJECTED is terminal
    assert rejected["state"] == "REJECTED"
    assert len(audit_log.read_events(uri, "LIFECYCLE_VIOLATION")) == 2


def test_only_active_documents_are_indexable(env, corpus):
    rec = lm.ingest(corpus / "study_001.pdf")
    with pytest.raises(LifecycleViolationError):
        lm.assert_indexable(rec["document_uri"])
    approve(rec["document_uri"])
    lm.assert_indexable(rec["document_uri"])                           # no error now


def test_scanned_pdf_cannot_be_approved(env, corpus):
    rec = lm.ingest(corpus / "study_006.pdf")
    assert rec["state"] == "UNDER_REVIEW"
    with pytest.raises(MetadataIncompleteError):
        approve(rec["document_uri"], metadata_updates={"study_type": "HCP Perception Survey",
                                                       "therapeutic_area": "Immunology", "geography": "EU"})


def test_uncontrolled_term_is_refused(env, corpus):
    rec = lm.ingest(corpus / "study_001.pdf")
    with pytest.raises(MetadataIncompleteError, match="uncontrolled_term"):
        approve(rec["document_uri"], metadata_updates={"therapeutic_area": "Astrology"})


def test_archive_and_reinstate_round_trip(env, corpus):
    rec = lm.ingest(corpus / "study_001.pdf")
    approve(rec["document_uri"])
    assert lm.archive(rec["document_uri"], "u_admin")["state"] == "ARCHIVED"
    assert lm.reinstate(rec["document_uri"], "u_admin")["state"] == "ACTIVE"


def test_approving_a_new_version_can_archive_the_old_one(env, corpus):
    old = lm.ingest(corpus / "study_013.pptx")
    approve(old["document_uri"])
    new = lm.ingest(corpus / "study_013_update.pdf")
    assert new["assessment"]["dedup"]["verdict"] == "new_version"
    out = approve(new["document_uri"], archive_superseded=True)
    assert out["supersedes"] == old["document_uri"]
    assert lm.get_state(old["document_uri"]) == "ARCHIVED"


# ------------------------------------------------------------------ ABAC
def test_roles_get_different_document_sets(env, corpus):
    specs = [("study_001.pdf", "RESTRICTED"), ("study_028.pdf", "INTERNAL"), ("study_021.docx", "PUBLIC"),
             ("study_014.pdf", "CONFIDENTIAL")]
    for name, sens in specs:
        rec = lm.ingest(corpus / name)
        approve(rec["document_uri"], sensitivity_level=sens)
    recs = lm.list_documents()
    seen = {u["role"]: sum(1 for r in recs if abac.view_level(u, r) == "full") for u in abac.list_users()}
    assert seen["VIEWER"] < seen["ANALYST"] <= seen["SPOC"] <= seen["ADMIN"] == 4
    viewer = abac.get_user("u_viewer")
    for r in recs:                                                       # viewer never sees RESTRICTED+
        if abac.view_level(viewer, r) == "full":
            assert r["access"]["sensitivity_level"] in ("PUBLIC", "INTERNAL")


def test_viewer_cannot_review_and_denial_is_audited(env, corpus):
    rec = lm.ingest(corpus / "study_001.pdf")
    with pytest.raises(WatsonAccessDeniedError):
        lm.review(rec["document_uri"], True, "u_viewer", sensitivity_level="INTERNAL")
    denied = audit_log.read_events(rec["document_uri"], "ABAC_DENIED")
    assert denied and denied[0]["details"]["role"] == "VIEWER"


def test_spoc_can_only_review_inside_their_area(env, corpus):
    onc = lm.ingest(corpus / "study_001.pdf")        # Oncology
    cardio = lm.ingest(corpus / "study_023.docx")    # Cardiology
    assert onc["metadata"]["therapeutic_area"] == "Oncology" and cardio["metadata"]["therapeutic_area"] == "Cardiology"
    assert lm.review(onc["document_uri"], True, "u_spoc", sensitivity_level="INTERNAL")["state"] == "ACTIVE"
    with pytest.raises(WatsonAccessDeniedError):
        lm.review(cardio["document_uri"], True, "u_spoc", sensitivity_level="INTERNAL")


def test_uploader_sees_own_pending_document_as_summary_only(env, corpus):
    rec = lm.ingest(corpus / "study_001.pdf", actor="u_viewer")
    viewer, analyst = abac.get_user("u_viewer"), abac.get_user("u_analyst")
    assert abac.view_level(viewer, rec) == "summary"
    assert abac.view_level(analyst, rec) is None


# ------------------------------------------------------------------ expiry and quality
def test_expiry_scanner_returns_expired_documents_to_review(env, corpus):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds")
    rec = lm.ingest(corpus / "study_037.txt", ingest_timestamp=old_ts)   # KOL interview: 365-day retention
    out = approve(rec["document_uri"])
    assert out["expiry_date"] < datetime.now(timezone.utc).date().isoformat()
    moved = expiry_manager.scan_expired()
    assert moved == [rec["document_uri"]] and lm.get_state(rec["document_uri"]) == "UNDER_REVIEW"
    assert audit_log.read_events(rec["document_uri"], "EXPIRY_TRIGGERED")
    assert any("Expired" in n for n in lm.get_record(rec["document_uri"])["reviewer_notes"])


def test_expiring_within_90_days_lists_soonest_first(env, corpus):
    ts = (datetime.now(timezone.utc) - timedelta(days=330)).isoformat(timespec="seconds")
    rec = lm.ingest(corpus / "study_037.txt", ingest_timestamp=ts)
    approve(rec["document_uri"])
    soon = expiry_manager.expiring_within(90)
    assert [r["document_uri"] for r in soon] == [rec["document_uri"]] and 0 < soon[0]["days_left"] <= 40


def test_quality_score_components_and_ordering():
    assert quality_scorer.hcp_sample_size("The sample size was n=320.") == 320
    assert quality_scorer.hcp_sample_size("a survey of 45 oncologists") == 45
    assert quality_scorer.sample_size_score(300) == 1.0 and quality_scorer.sample_size_score(10) == 0.1
    assert quality_scorer.sample_size_score(150) < quality_scorer.sample_size_score(250)
    now = datetime.now(timezone.utc)
    assert quality_scorer.freshness(now.isoformat(), now) == pytest.approx(1.0)
    assert quality_scorer.freshness((now - timedelta(days=800)).isoformat(), now) == 0.0


def test_quality_scores_vary_across_the_corpus(env, corpus, manifest):
    scores = []
    for name in ("study_001.pdf", "study_004.docx", "study_009.pdf", "study_021.docx", "study_028.pdf", "study_041.pptx"):
        rec = lm.ingest(corpus / name, ingest_timestamp=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds"))
        scores.append(approve(rec["document_uri"])["quality"]["quality_score"])
    assert max(scores) - min(scores) > 0.15                 # not clustered around one value
