# ingest.py
# ingest.py
import os
from pathlib import Path
from typing import List, Tuple
import fitz # pymupdf
import pandas as pd
import re


CHUNK_SIZE = 800 # words




def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()




def read_md(path: str) -> str:
    return read_text_file(path)




def read_pdf(path: str) -> str:
    try:
        doc = fitz.open(path)
        pages = [p.get_text("text") for p in doc]
        return "\n".join(pages)
    except Exception:
        # fallback to returning an empty string on parse error
        return ""




def read_csv_excel(path: str) -> str:
    try:
        if path.lower().endswith((".xls", ".xlsx")):
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path)
        return df.to_csv(index=False)
    except Exception:
        return ""




def parse_file(path: str) -> str:
    path = str(path)
    p = path.lower()
    if p.endswith(".pdf"):
        return read_pdf(path)
    if p.endswith(".md") or p.endswith(".txt"):
        return read_text_file(path)
    if p.endswith((".csv", ".xlsx", ".xls")):
        return read_csv_excel(path)
    # fallback: try reading as text
    return read_text_file(path)




_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")




def chunk_text(text: str, chunk_size=CHUNK_SIZE) -> List[str]:
    if not text or not text.strip():
        return []
    words = text.split()
    if len(words) <= chunk_size:
        return [text.strip()]


    # smarter: split on sentence boundaries, accumulate until chunk_size reached
    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks = []
    cur = []
    cur_len = 0
    for s in sentences:
        s_words = s.split()
        cur.append(s)
        cur_len += len(s_words)
        if cur_len >= chunk_size:
            chunks.append(" ".join(cur).strip())
            cur = []
            cur_len = 0
    if cur:
        chunks.append(" ".join(cur).strip())
    return chunks




def ingest_file(path: str) -> List[Tuple[str, str]]:
    text = parse_file(path)
    chunks = chunk_text(text)
    return [(str(path), c) for c in chunks]