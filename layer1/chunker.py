"""Chunking (Section 4.2): two strategies, same chunk schema.

Token = whitespace-delimited word. A real tokenizer would count slightly
differently; word tokens keep char offsets exact and the comparison fair.
Invariant for both strategies: chunk["text"] == raw_text[char_start:char_end].
"""
import re

from . import config


def _chunk_uri(doc_uri: str, index: int) -> str:
    num = doc_uri.split("_")[-1]
    return f"Watson:Chunk_{num}_{index:03d}"


def _make(doc_uri, index, raw, start, end, strategy, title=None):
    text = raw[start:end]
    return {
        "chunk_uri": _chunk_uri(doc_uri, index),
        "parent_document_uri": doc_uri,
        "chunk_index": index,
        "char_start": start,
        "char_end": end,
        "token_count": len(text.split()),
        "strategy": strategy,
        "section_title": title,
        "text": text,
    }


def _token_spans(raw: str, lo: int = 0, hi: int | None = None):
    seg = raw[lo:hi]
    return [(lo + m.start(), lo + m.end()) for m in re.finditer(r"\S+", seg)]


def chunk_fixed(raw: str, doc_uri: str, n: int = None, overlap: float = None) -> list:
    """Strategy A: N-token windows, adjacent chunks overlap by `overlap` (20%)."""
    n = n or config.FIXED_CHUNK_TOKENS
    overlap = config.FIXED_CHUNK_OVERLAP if overlap is None else overlap
    spans = _token_spans(raw)
    step = max(1, int(n * (1 - overlap)))
    chunks, i = [], 0
    while i < len(spans):
        window = spans[i:i + n]
        chunks.append(_make(doc_uri, len(chunks), raw, window[0][0], window[-1][1], f"fixed_{n}"))
        if i + n >= len(spans):
            break
        i += step
    return chunks


def _split_long(raw: str, start: int, end: int, limit: int):
    """Split [start,end) at sentence boundaries into pieces of at most `limit` tokens."""
    sentences, pos = [], start
    for m in re.finditer(r"(?<=[.!?])\s+", raw[start:end]):
        sentences.append((pos, start + m.start()))
        pos = start + m.end()
    sentences.append((pos, end))
    pieces, cur_start, cur_tokens, last_end = [], None, 0, None
    for s, e in sentences:
        t = len(raw[s:e].split())
        if cur_start is not None and cur_tokens + t > limit:
            pieces.append((cur_start, last_end))
            cur_start, cur_tokens = None, 0
        if cur_start is None:
            cur_start = s
        cur_tokens += t
        last_end = e
    if cur_start is not None:
        pieces.append((cur_start, last_end))
    return pieces


def chunk_structural(parsed, doc_uri: str) -> list:
    """Strategy B: DOCX heading sections, PDF pages (split if > 600 tokens),
    PPTX one chunk per slide, TXT paragraph groups."""
    raw, chunks = parsed.raw_text, []
    if parsed.format == "txt":
        group_start, group_end, tokens = None, None, 0
        for u in parsed.units:
            t = len(raw[u["start"]:u["end"]].split())
            if group_start is not None and tokens + t > config.TXT_GROUP_TOKENS:
                chunks.append(_make(doc_uri, len(chunks), raw, group_start, group_end, "structural", "Paragraph group"))
                group_start, tokens = None, 0
            if group_start is None:
                group_start = u["start"]
            group_end, tokens = u["end"], tokens + t
        if group_start is not None:
            chunks.append(_make(doc_uri, len(chunks), raw, group_start, group_end, "structural", "Paragraph group"))
        return chunks
    for u in parsed.units:
        t = len(raw[u["start"]:u["end"]].split())
        if parsed.format == "pdf" and t > config.PDF_PAGE_SPLIT_TOKENS:
            for s, e in _split_long(raw, u["start"], u["end"], config.PDF_PAGE_SPLIT_TOKENS):
                chunks.append(_make(doc_uri, len(chunks), raw, s, e, "structural", u["title"]))
        else:
            chunks.append(_make(doc_uri, len(chunks), raw, u["start"], u["end"], "structural", u["title"]))
    return chunks


def chunk_document(parsed, doc_uri: str, strategy: str = None) -> list:
    strategy = strategy or config.CHUNK_STRATEGY
    if strategy == "fixed":
        return chunk_fixed(parsed.raw_text, doc_uri)
    if strategy == "structural":
        return chunk_structural(parsed, doc_uri)
    raise ValueError(f"Unknown chunk strategy: {strategy}")
