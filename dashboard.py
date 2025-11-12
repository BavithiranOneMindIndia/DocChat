# dashboard.py
"""
Smart Document Dashboard + Collection Report for ProDocChat

Usage:
    from dashboard import show_dashboard, show_collection_report

Integrate into main.py menu:
    if menu == "Dashboard": show_dashboard(USER_DIR, COLLECTIONS_JSON)
    if menu == "Collection Report": show_collection_report(USER_DIR, COLLECTIONS_JSON)

"""

import os
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from collections import Counter, defaultdict
import re
from typing import Iterable
import streamlit as st
import pandas as pd

# For reading PDFs
try:
    import fitz  # pymupdf
except Exception:
    fitz = None

# ---------- helper utilities ----------

SUPPORTED_EXTS = [".pdf", ".txt", ".md", ".csv", ".xlsx", ".xls"]


def human_size(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024.0:
            return f"{n:3.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def read_file_text(path: str, sample_kb: int = 64) -> str:
    """Read file and return text. For pdfs use pymupdf if available.
    For large files we sample first sample_kb kilobytes for speed."""
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        if suffix == ".pdf" and fitz is not None:
            try:
                doc = fitz.open(str(p))
                # read text from all pages but limit to some characters for speed
                texts = []
                for i, page in enumerate(doc):
                    texts.append(page.get_text("text"))
                    # stop if length beyond sample_kb limit
                    if sum(len(t) for t in texts) > sample_kb * 1024:
                        break
                return "\n".join(texts)
            except Exception:
                pass
        if suffix in [".txt", ".md", ".csv"]:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(sample_kb * 1024)
        if suffix in [".xlsx", ".xls"]:
            try:
                df = pd.read_excel(p, engine="openpyxl" if p.suffix == ".xlsx" else None)
                # convert to csv-ish text (first N rows)
                return df.head(100).to_csv(index=False)
            except Exception:
                try:
                    df = pd.read_csv(p)
                    return df.head(100).to_csv(index=False)
                except Exception:
                    return ""
        # fallback: read as binary sample and try decode
        with open(p, "rb") as f:
            data = f.read(sample_kb * 1024)
            try:
                return data.decode("utf-8", errors="ignore")
            except Exception:
                return ""
    except Exception:
        return ""


def tokenize(text: str) -> List[str]:
    """Very simple tokenizer for keywords."""
    if not text:
        return []
    # remove punctuation-ish characters, lowercase
    # keep simple: split on whitespace after removing some punctuation
    for ch in [",", ".", ":", ";", "(", ")", "[", "]", "{", "}", "\"", "'", "“", "”", "—", "-", "_", "/", "\\"]:
        text = text.replace(ch, " ")
    tokens = [t.strip().lower() for t in text.split() if t.strip()]
    return tokens


# small english stoplist
STOPWORDS = {
    "the", "and", "is", "in", "it", "of", "to", "a", "for", "on", "that", "this",
    "with", "as", "are", "was", "be", "by", "or", "an", "at", "from", "we", "you",
    "not", "have", "has", "but", "they", "their", "which"
}


def top_keywords_from_text(text: str, top_n: int = 20) -> List[Tuple[str, int]]:
    tokens = tokenize(text)
    tokens = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    ctr = Counter(tokens)
    return ctr.most_common(top_n)


# ---------- collection scanning & report building ----------

def _load_collections(collections_json: str) -> Dict[str, Dict]:
    if os.path.exists(collections_json):
        try:
            with open(collections_json, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _scan_collection(folder: str, progress_callback: Optional[Any] = None) -> Tuple[List[Dict], Dict]:
    """
    Scan a collection folder and return:
    - files_meta: list of dicts with file metadata and sample text word counts
    - summary: aggregated counters and totals
    """
    files_meta = []
    total_files = 0
    total_bytes = 0
    total_words = 0
    ext_counter = Counter()
    word_counter = Counter()

    p = Path(folder)
    if not p.exists():
        return files_meta, {"total_files": 0, "total_bytes": 0, "total_words": 0, "ext_counts": {}, "top_words": []}

    files = [f for f in p.rglob("*.*") if f.suffix.lower() in SUPPORTED_EXTS and f.is_file()]
    total_files = len(files)
    if total_files == 0:
        return [], {"total_files": 0, "total_bytes": 0, "total_words": 0, "ext_counts": {}, "top_words": []}

    for idx, f in enumerate(files, start=1):
        try:
            size = f.stat().st_size
            mtime = f.stat().st_mtime
            text = read_file_text(str(f), sample_kb=64)
            tokens = tokenize(text)
            words = len(tokens)
            total_words += words
            total_bytes += size
            ext = f.suffix.lower()
            ext_counter[ext] += 1
            word_counter.update([t for t in tokens if t not in STOPWORDS and len(t) > 2])

            files_meta.append({
                "name": str(f.relative_to(p)),
                "full_path": str(f),
                "ext": ext,
                "size_bytes": size,
                "size_human": human_size(size),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
                "words_sampled": words,
                "est_chunks": int(max(1, words / 800)),
            })
        except Exception:
            # skip problematic files
            continue

        if progress_callback:
            try:
                progress_callback(idx / total_files)
            except Exception:
                pass

    summary = {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_words": total_words,
        "ext_counts": dict(ext_counter),
        "top_words": word_counter.most_common(50),
    }
    return files_meta, summary


# ---------- UI functions ----------

def show_dashboard(USER_DIR: str, COLLECTIONS_JSON: str):
    """Existing dashboard (keeps brief summary)"""
    st.title("Smart Document Dashboard")
    st.markdown("Overview of your collections, files, and indexing status.")

    collections = _load_collections(COLLECTIONS_JSON)
    col_count = len(collections)

    total_files = 0
    total_chunks = 0
    total_bytes = 0
    per_collection = []

    for name, meta in collections.items():
        folder = meta.get("path", "")
        p = Path(folder)
        if not p.exists():
            files = []
        else:
            files = [f for f in p.rglob("*.*") if f.suffix.lower() in SUPPORTED_EXTS and f.is_file()]
        files_count = len(files)
        # quick approximate chunks: approximate words by bytes/6, then divide by 800
        size_bytes = sum(f.stat().st_size for f in files if f.exists())
        chunks = int((size_bytes / 6) / 800) if size_bytes > 0 else 0

        total_files += files_count
        total_chunks += chunks
        total_bytes += size_bytes
        per_collection.append({
            "collection": name,
            "path": folder,
            "files": files_count,
            "est_chunks": chunks,
            "size_bytes": size_bytes,
            "last_indexed": meta.get("last_indexed"),
            "watching": bool(meta.get("watching")),
        })

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Collections", col_count)
    c2.metric("Files (scanned)", total_files)
    c3.metric("Estimated chunks", total_chunks)
    c4.metric("Total size", human_size(total_bytes))

    st.markdown("---")
    if per_collection:
        df_pc = pd.DataFrame(per_collection)
        st.subheader("Files per collection")
        st.bar_chart(df_pc.set_index("collection")["files"])

        st.subheader("Collections details")
        for row in per_collection:
            with st.expander(f"{row['collection']} ({row['files']} files)"):
                st.write(f"Path: `{row['path']}`")
                st.write(f"Estimated chunks: {row['est_chunks']}")
                st.write(f"Size: {human_size(row['size_bytes'])}")
                st.write(f"Last indexed: {row.get('last_indexed')}")
                st.write(f"Watching: {row.get('watching')}")
    else:
        st.info("No collections found. Create one from the Create Collection menu.")

    st.markdown("---")
    st.subheader("Quick actions")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Rescan / Refresh dashboard"):
            st.experimental_rerun()
    with col_b:
        if st.button("Open collections.json"):
            try:
                with open(COLLECTIONS_JSON, "r", encoding="utf-8") as f:
                    data = json.load(f)
                st.json(data)
            except Exception as e:
                st.error(f"Unable to open collections.json: {e}")


def show_collection_report(USER_DIR: str, COLLECTIONS_JSON: str):
    """Interactive collection-wise consolidated report builder."""
    st.title("Collection Consolidated Report")
    cols = _load_collections(COLLECTIONS_JSON)
    if not cols:
        st.info("No collections found. Create one first.")
        return

    collection_names = sorted(list(cols.keys()))
    selected = st.selectbox("Select collection", collection_names)
    meta = cols.get(selected, {})
    folder = meta.get("path", "")

    st.markdown(f"**Collection:** {selected} — `{folder}`")
    if not folder or not Path(folder).exists():
        st.error("Collection folder not found on disk. Update path from Collections page.")
        return

    st.markdown("Scan options:")
    with st.form("scan_options"):
        sample_kb = st.number_input("Sample KB per file (reading speed vs completeness)", value=64, min_value=8, max_value=1024, step=8)
        compute_keywords = st.checkbox("Compute top keywords (may be slower)", value=True)
        run_btn = st.form_submit_button("Run consolidated report")
    if not run_btn:
        st.info("Adjust options and click 'Run consolidated report' to start scanning files.")
        return

    status = st.empty()
    progress_bar = st.progress(0.0)

    def progress_cb(fraction):
        try:
            progress_bar.progress(min(1.0, max(0.0, fraction)))
        except Exception:
            pass

    # actual scan
    with st.spinner("Scanning files... this may take a while for large collections"):
        files_meta, summary = _scan_collection(folder, progress_callback=progress_cb)

    progress_bar.empty()
    status.success(f"Scan complete. {summary['total_files']} files scanned, {len(files_meta)} items collected.")

    # summary tiles
    st.subheader("Collection summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Files", summary["total_files"])
    c2.metric("Total size", human_size(summary["total_bytes"]))
    c3.metric("Estimated words (sampled)", summary["total_words"])
    c4.metric("Unique ext types", len(summary["ext_counts"]))

    st.markdown("---")

    # top file types
    st.subheader("File types")
    if summary["ext_counts"]:
        df_ext = pd.DataFrame(list(summary["ext_counts"].items()), columns=["ext", "count"]).sort_values("count", ascending=False)
        st.table(df_ext)
    else:
        st.write("No file types found.")

    st.markdown("---")
    # top keywords
    if compute_keywords:
        st.subheader("Top keywords (sampled)")
        top_words = summary.get("top_words", [])[:50]
        if top_words:
            df_kw = pd.DataFrame(top_words, columns=["keyword", "count"])
            st.table(df_kw)
        else:
            st.write("No keywords extracted.")

    st.markdown("---")
    st.subheader("Per-file details (sampled)")
    if files_meta:
        df_files = pd.DataFrame(files_meta)
        # allow sorting
        sort_by = st.selectbox("Sort files by", ["words_sampled", "size_bytes", "modified", "name"], index=0)
        ascending = st.checkbox("Ascending order", value=False)
        try:
            df_files_sorted = df_files.sort_values(sort_by, ascending=ascending)
        except Exception:
            df_files_sorted = df_files
        st.dataframe(df_files_sorted.reset_index(drop=True), height=400)

        # export buttons
        st.markdown("---")
        st.subheader("Export report")
        csv = df_files_sorted.to_csv(index=False)
        json_report = {
            "collection": selected,
            "folder": folder,
            "summary": summary,
            "files": df_files_sorted.to_dict(orient="records")
        }
        st.download_button("Download CSV (files)", csv, file_name=f"{selected}_files_report.csv")
        st.download_button("Download JSON (full)", json.dumps(json_report, indent=2), file_name=f"{selected}_report.json")
    else:
        st.info("No files discovered in the collection.")

    st.markdown("---")
    st.caption("Report is based on sampled text from files (configurable). For exact indexing metrics, query your vector store / Chroma metadata.")


def extract_headlines_from_text(text: str, max_headlines: int = 5) -> List[str]:
    """
    Heuristic headline extractor:
    - Markdown headings (#, ##, etc)
    - First non-empty line (title)
    - Short lines (<= 10 words and <= 80 chars) likely serving as section titles
    - Lines that end with ':' (e.g., 'Key points:')
    Returns up to max_headlines candidate headlines (preserving order of appearance).
    """
    if not text:
        return []

    headlines = []
    seen = set()

    # normalize newlines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 1) Markdown headings (#)
    for ln in lines:
        if re.match(r"^#{1,6}\s+", ln):
            h = re.sub(r"^#{1,6}\s+", "", ln).strip()
            if h and h not in seen:
                headlines.append(h)
                seen.add(h)
            if len(headlines) >= max_headlines:
                return headlines

    # 2) First non-empty line (title)
    if lines:
        first = lines[0]
        if first and first not in seen and len(first.split()) <= 12 and len(first) <= 120:
            headlines.append(first)
            seen.add(first)
    if len(headlines) >= max_headlines:
        return headlines

    # 3) Short lines (possible section headings)
    for ln in lines[1:]:
        word_count = len(ln.split())
        if word_count <= 10 and len(ln) <= 80 and not ln.endswith(".") and ":" not in ln:
            # skip typical "date-like" or "author" lines: check for many digits
            if sum(c.isdigit() for c in ln) > 6:
                continue
            if ln not in seen:
                headlines.append(ln)
                seen.add(ln)
        if len(headlines) >= max_headlines:
            return headlines

    # 4) Lines ending with colon (like "Summary:")
    for ln in lines:
        if ln.endswith(":") and ln not in seen:
            headlines.append(ln.rstrip(":"))
            seen.add(ln)
        if len(headlines) >= max_headlines:
            return headlines

    # 5) fallback: take the top N most frequent short phrases (basic)
    tokens = tokenize(" ".join(lines))
    frequent = Counter([t for t in tokens if t not in STOPWORDS and len(t) > 3]).most_common(10)
    for word, _ in frequent:
        if word not in seen:
            headlines.append(word)
            seen.add(word)
        if len(headlines) >= max_headlines:
            break

    return headlines[:max_headlines]


def llm_headlines_for_text(text: str, top_n: int = 3, system_prompt: str | None = None) -> List[str]:
    """
    Call your Azure chat LLM to extract `top_n` headlines/key-points from `text`.
    Returns a list of short strings. This is optional and will be called only
    if user enables LLM extraction (checkbox).
    """
    # Keep local import to avoid failing when azure_client couldn't be configured
    try:
        from azure_client import chat_with_context
    except Exception:
        return []

    if not system_prompt:
        system_prompt = (
            "You are a concise information extractor. "
            "Given file contents, return a JSON array of up to {n} short headline strings "
            "representing the most important points or section titles in the file. "
            "Keep each headline ≤ 120 characters."
        ).replace("{n}", str(top_n))

    try:
        # we pass the file text as the 'context' to the chat wrapper
        resp = chat_with_context(system_prompt, "Please extract headlines as a JSON array.", text, temperature=0.0)
        # try to parse a JSON array from the response
        import json as _json
        # some models return plain lines; try to recover
        try:
            parsed = _json.loads(resp)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed[:top_n]]
        except Exception:
            # fallback: split lines and pick short ones
            lines = [l.strip() for l in resp.splitlines() if l.strip()]
            candidates = []
            for l in lines:
                # if response has a JSON-like array on a line, try to eval safely
                if l.startswith("[") and l.endswith("]"):
                    try:
                        parsed = _json.loads(l)
                        if isinstance(parsed, list):
                            candidates.extend([str(x).strip() for x in parsed])
                            continue
                    except Exception:
                        pass
                # remove numbering like "1. " or "- "
                l2 = re.sub(r"^\s*[\-\d\.\)]\s*", "", l)
                if 3 <= len(l2) <= 160:
                    candidates.append(l2)
            return candidates[:top_n]
    except Exception:
        return []


def show_collection_report_with_headlines(USER_DIR: str, COLLECTIONS_JSON: str):
    """Updated collection report that highlights headlines / important details per file."""
    st.title("Collection Consolidated Report — Headlines & Highlights")
    cols = _load_collections(COLLECTIONS_JSON)
    if not cols:
        st.info("No collections found. Create one first.")
        return

    collection_names = sorted(list(cols.keys()))
    selected = st.selectbox("Select collection", collection_names)
    meta = cols.get(selected, {})
    folder = meta.get("path", "")

    st.markdown(f"**Collection:** {selected} — `{folder}`")
    if not folder or not Path(folder).exists():
        st.error("Collection folder not found on disk. Update path from Collections page.")
        return

    st.markdown("Scan options:")
    with st.form("scan_options_headlines"):
        sample_kb = st.number_input("Sample KB per file (reading speed vs completeness)", value=64, min_value=8, max_value=1024, step=8)
        compute_keywords = st.checkbox("Compute top keywords (may be slower)", value=True)
        use_llm = st.checkbox("Use LLM to refine headlines (optional; costs API calls)", value=False)
        llm_top_n = st.number_input("LLM headlines per file", min_value=1, max_value=6, value=3)
        run_btn = st.form_submit_button("Run consolidated report")
    if not run_btn:
        st.info("Adjust options and click 'Run consolidated report' to start scanning files.")
        return

    status = st.empty()
    progress_bar = st.progress(0.0)
    def progress_cb(fraction):
        try:
            progress_bar.progress(min(1.0, max(0.0, fraction)))
        except Exception:
            pass

    with st.spinner("Scanning files and extracting headlines..."):
        files_meta, summary = _scan_collection(folder, progress_callback=progress_cb)

    # For each file, run heuristic headline extraction; optionally refine with LLM
    consolidated_headlines = Counter()
    for idx, fm in enumerate(files_meta):
        try:
            txt = read_file_text(fm["full_path"], sample_kb=sample_kb)
            heur = extract_headlines_from_text(txt, max_headlines=6)
            fm["headlines_heuristic"] = heur
            for h in heur:
                consolidated_headlines[h] += 1

            if use_llm and txt.strip():
                # optional: show a tiny progress or throttle
                llm_h = llm_headlines_for_text(txt, top_n=int(llm_top_n))
                fm["headlines_llm"] = llm_h
                for h in llm_h:
                    consolidated_headlines[h] += 1
        except Exception:
            fm["headlines_heuristic"] = []
            fm["headlines_llm"] = []

    progress_bar.empty()
    status.success(f"Scan complete. {summary['total_files']} files scanned, {len(files_meta)} items collected.")

    # Display consolidated headline overview
    st.subheader("Collection headline summary (consolidated)")
    if consolidated_headlines:
        ch = consolidated_headlines.most_common(30)
        df_ch = pd.DataFrame(ch, columns=["headline", "count"])
        st.table(df_ch)
    else:
        st.write("No headlines extracted for this collection.")

    st.markdown("---")
    st.subheader("Per-file highlights")
    for fm in files_meta:
        with st.expander(f"{fm['name']} — {fm['size_human']} — {fm['modified']}"):
            st.write(f"**Heuristic headlines:**")
            if fm.get("headlines_heuristic"):
                for h in fm["headlines_heuristic"]:
                    st.markdown(f"- {h}")
            else:
                st.write("_none_")

            if use_llm:
                st.write("**LLM headlines (refined):**")
                if fm.get("headlines_llm"):
                    for h in fm["headlines_llm"]:
                        st.markdown(f"- {h}")
                else:
                    st.write("_none_ (LLM could not extract or call failed)_")

            # small preview excerpt
            try:
                excerpt = read_file_text(fm["full_path"], sample_kb=12)
                if excerpt:
                    st.markdown("**Preview (sample):**")
                    st.code(excerpt[:2000])
            except Exception:
                pass

    # Exports
    st.markdown("---")
    st.subheader("Export report (with headlines)")
    df_files = pd.DataFrame(files_meta)
    csv = df_files.to_csv(index=False)
    json_report = {
        "collection": selected,
        "folder": folder,
        "summary": summary,
        "files": df_files.to_dict(orient="records")
    }
    st.download_button("Download CSV (files with headlines)", csv, file_name=f"{selected}_files_with_headlines.csv")
    st.download_button("Download JSON (full)", json.dumps(json_report, indent=2), file_name=f"{selected}_report_with_headlines.json")

    st.markdown("---")
    st.caption("Headlines are extracted heuristically. Use the LLM option for higher-quality, semantic headlines (may be slower and consume API credits).")

