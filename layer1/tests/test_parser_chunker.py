import pytest

from layer1 import chunker
from layer1.errors import UnsupportedFormatError
from layer1.parser import parse_file


def test_every_generated_file_parses_to_the_same_shape(corpus):
    files = [p for p in corpus.iterdir() if p.suffix in (".pdf", ".docx", ".pptx", ".txt")]
    assert len(files) >= 40                               # requirement: at least 40 documents
    assert {p.suffix for p in files} == {".pdf", ".docx", ".pptx", ".txt"}
    for p in files:
        d = parse_file(p)
        assert d.format == p.suffix[1:] and d.source_filename == p.name
        assert isinstance(d.parse_warnings, list) and isinstance(d.extracted_metadata, dict)


def test_scanned_pdf_is_flagged_not_silently_empty(corpus):
    d = parse_file(corpus / "study_006.pdf")
    assert d.word_count == 0
    assert any(w.startswith("SCANNED_PDF") for w in d.parse_warnings)


def test_pptx_image_only_slide_is_flagged_and_boxes_sorted_top_left(corpus):
    d = parse_file(corpus / "study_013.pptx")
    assert any(w.startswith("NON_TEXTUAL_SLIDE") for w in d.parse_warnings)
    d2 = parse_file(corpus / "study_016.pptx")             # lower text box was added first
    assert d2.raw_text.index("First takeaway") < d2.raw_text.index("Second takeaway")


def test_docx_table_cells_are_extracted_and_headings_recorded(corpus):
    d = parse_file(corpus / "study_007.docx")
    assert "High-volume prescribers" in d.raw_text and "Sentiment index" in d.raw_text
    assert d.extracted_metadata["headings"][0]["level"] == 0   # Title style


def test_txt_encoding_is_detected_not_assumed(corpus):
    d = parse_file(corpus / "study_039.txt")
    assert "Jürgen Müller" in d.raw_text
    assert d.extracted_metadata["encoding"].lower() in ("windows-1252", "iso-8859-1", "cp1252")


def test_reportlab_placeholder_metadata_becomes_none(corpus):
    d = parse_file(corpus / "study_008.pdf")               # written with no title and no author
    assert d.extracted_metadata["title"] is None and d.extracted_metadata["author"] is None


def test_unsupported_extension(tmp_path):
    p = tmp_path / "x.xlsx"
    p.write_bytes(b"x")
    with pytest.raises(UnsupportedFormatError):
        parse_file(p)


@pytest.mark.parametrize("name", ["study_001.pdf", "study_007.docx", "study_013.pptx", "study_037.txt"])
def test_both_chunk_strategies_keep_exact_offsets(corpus, name):
    d = parse_file(corpus / name)
    for chunks in (chunker.chunk_structural(d, "Watson:Doc_001"), chunker.chunk_fixed(d.raw_text, "Watson:Doc_001")):
        assert chunks
        for c in chunks:
            assert c["text"] == d.raw_text[c["char_start"]:c["char_end"]]
            assert c["token_count"] == len(c["text"].split())
            assert c["parent_document_uri"] == "Watson:Doc_001"


def test_fixed_chunks_overlap_by_20_percent():
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = chunker.chunk_fixed(text, "Watson:Doc_001", n=256)
    assert all(c["token_count"] <= 256 for c in chunks)
    first, second = chunks[0]["text"].split(), chunks[1]["text"].split()
    overlap = len(set(first) & set(second))
    assert overlap == 256 - int(256 * 0.8)                 # 20% of 256 tokens = 52 (51 from int())


def test_structural_pdf_page_over_600_tokens_is_split(tmp_path):
    from layer1.parser import ParsedDocument
    raw = " ".join("Sentence number %d is here." % i for i in range(300))
    pd = ParsedDocument(raw, 1, "pdf", "x.pdf", {}, [], [{"kind": "page", "title": "Page 1", "start": 0, "end": len(raw)}])
    chunks = chunker.chunk_structural(pd, "Watson:Doc_001")
    assert len(chunks) >= 2 and all(c["token_count"] <= 600 for c in chunks)
