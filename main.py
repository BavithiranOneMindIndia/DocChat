# main.py
"""
ProDocChat - main Streamlit UI (updated to keep sidebar visible while editing Settings)

Changes:
- Settings is a top-level menu item (keeps sidebar visible).
- "Settings / Edit Azure config" button will switch to the Settings menu.
- First-run config enforcement remains (show_settings_if_missing).
- No st.stop() that hides sidebar; settings render in main area.
"""

import os
import time
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from dashboard import show_dashboard, show_collection_report_with_headlines
import streamlit as st

# local modules
from config_ui import show_settings_if_missing, show_settings_editor
from utils import load_or_copy_config, get_user_data_dir, ensure_collections_dir
from ingest import ingest_file
from azure_client import embed_texts, chat_with_context
from store import add_documents, query

# filesystem watcher
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# tkinter for native folder picker (local only)
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    tk = None
    filedialog = None

# -------------------------
# Load config & user dir
# -------------------------
cfg = load_or_copy_config()
APP_NAME = cfg.get("app", "app_name", fallback="ProDocChat").strip() or "ProDocChat"
USER_DIR = get_user_data_dir(APP_NAME)
ensure_collections_dir(cfg)
COLLECTIONS_JSON = os.path.join(USER_DIR, "collections.json")
CHATS_DIR = os.path.join(USER_DIR, "chats")
os.makedirs(CHATS_DIR, exist_ok=True)

# Enforce config on first run (blocks until filled)
show_settings_if_missing(cfg, USER_DIR)
cfg = load_or_copy_config()  # reload after potential save

# -------------------------
# Streamlit page config
# -------------------------
st.set_page_config(
    page_title="ProDocChat", layout="wide", initial_sidebar_state="expanded"
)


# -------------------------
# Helpers
# -------------------------
def trigger_rerun() -> None:
    ts = str(int(time.time()))
    try:
        st.query_params = {"_refresh": ts}
    except Exception:
        st.session_state["_refresh_flag"] = not st.session_state.get(
            "_refresh_flag", False
        )


def pick_folder_tk() -> Optional[str]:
    if tk is None or filedialog is None:
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory()
        root.destroy()
        if folder:
            return str(Path(folder))
        return None
    except Exception:
        return None


# -------------------------
# Collections persistence helpers
# -------------------------
def load_collections() -> Dict[str, Dict]:
    if os.path.exists(COLLECTIONS_JSON):
        try:
            with open(COLLECTIONS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_collections(data: Dict[str, Dict]) -> None:
    try:
        with open(COLLECTIONS_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        st.error(f"Failed to save collections: {e}")


collections = load_collections()


# -------------------------
# Chat persistence
# -------------------------
def sanitize_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def chat_file_for(collection_name: str) -> str:
    safe = sanitize_name(collection_name)
    return os.path.join(CHATS_DIR, f"{safe}.json")


def load_chat(collection_name: str) -> List[Dict]:
    path = chat_file_for(collection_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_chat(collection_name: str, messages: List[Dict]) -> None:
    path = chat_file_for(collection_name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=2)
    except Exception:
        pass


# -------------------------
# Watcher: incremental indexing
# -------------------------
_watchers: Dict[str, Any] = {}


class RebuildHandler(FileSystemEventHandler):
    def __init__(self, collection_name: str):
        super().__init__()
        self.collection = collection_name

    def on_created(self, event):
        if not event.is_directory:
            _process_file(event.src_path, self.collection)

    def on_modified(self, event):
        if not event.is_directory:
            _process_file(event.src_path, self.collection)


def _process_file(path: str, collection_name: str) -> None:
    try:
        items = ingest_file(path)
        if not items:
            return
        docs = [c for _, c in items]
        metas = [{"source": s} for s, _ in items]
        embs = embed_texts(docs)
        add_documents(collection_name, docs, metas, embs)
        with open(os.path.join(USER_DIR, "watcher.log"), "a", encoding="utf-8") as wf:
            wf.write(f"{time.asctime()}: indexed {path} into {collection_name}\n")
    except Exception as e:
        with open(os.path.join(USER_DIR, "watcher.log"), "a", encoding="utf-8") as wf:
            wf.write(
                f"{time.asctime()}: failed to index {path} into {collection_name}: {e}\n"
            )


def start_watching(collection_name: str, folder_path: str) -> None:
    if collection_name in _watchers:
        return
    try:
        handler = RebuildHandler(collection_name)
        observer = Observer()
        observer.schedule(handler, folder_path, recursive=True)
        observer.daemon = True
        observer.start()
        _watchers[collection_name] = observer
        collections.setdefault(collection_name, {})["watching"] = True
        save_collections(collections)
    except Exception as e:
        st.error(f"Failed to start watcher for {collection_name}: {e}")


def stop_watching(collection_name: str) -> None:
    obs = _watchers.pop(collection_name, None)
    if obs:
        try:
            obs.stop()
            obs.join(timeout=2)
        except Exception:
            pass
    collections.setdefault(collection_name, {})["watching"] = False
    save_collections(collections)


# Resume watchers at startup
if "watchers_resumed" not in st.session_state:
    for cname, meta in list(collections.items()):
        if meta.get("watching"):
            folder = meta.get("path")
            if folder and Path(folder).exists():
                try:
                    start_watching(cname, folder)
                except Exception:
                    with open(
                        os.path.join(USER_DIR, "watcher.log"), "a", encoding="utf-8"
                    ) as wf:
                        wf.write(
                            f"{time.asctime()}: failed to resume watcher for {cname}\n"
                        )
    st.session_state["watchers_resumed"] = True

# Ensure chat history in session
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = {}

# -------------------------
# CSS (layout & sticky input)
# -------------------------
st.markdown(
    """
    <style>
    .muted { color:#9aa0a6; font-size:13px; }
    .small { font-size:12px; color:#9aa0a6; }
    .card { background:#0f1112; padding:12px; border-radius:8px; margin-bottom:8px; border:1px solid #202427; }
    .left-title { font-weight:700; font-size:18px; margin-bottom:6px; }
    .chat-messages { height: calc(100vh - 260px); overflow:auto; padding:12px; background:#0b0c0e; border-radius:8px; border:1px solid #162022; }
    .chat-input-wrapper { position: sticky; bottom: 0; background: transparent; padding-top: 8px; margin-top: 8px; }
    .msg-user { background: #2b7a2b; color: #fff; padding: 10px 14px; border-radius: 14px; display:inline-block; max-width:80%; }
    .msg-assistant { background: #131417; color: #ddd; padding: 10px 14px; border-radius: 12px; display:inline-block; max-width:80%; border:1px solid #222; }
    .msg-ts { font-size: 11px; color: #9aa0a6; margin-top:4px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------
# Sidebar: app title + controls
# -------------------------
st.sidebar.title(APP_NAME)
st.sidebar.markdown("---")
st.sidebar.markdown("Data directory:")
st.sidebar.code(USER_DIR)
st.sidebar.markdown("Collections file:")
st.sidebar.code(COLLECTIONS_JSON)
st.sidebar.markdown("---")

# Button to quickly switch to Settings menu (keeps sidebar)
if st.sidebar.button("Settings / Edit Azure config"):
    st.session_state["menu_selected"] = "Settings"

if st.sidebar.button("Stop all watchers"):
    for c in list(_watchers.keys()):
        stop_watching(c)
    st.sidebar.success("Stopped watchers")
    trigger_rerun()

# Main menu choices (includes Settings)
menu = st.sidebar.radio(
    "Menu",
    [
        "Create Collection",
        "Collections",
        "Chat",
        "Settings",
        "Dashboard",
        "Collection Report",
    ],
    index=0,
    key="menu_selected",
)

# -------------------------
# Create Collection
# -------------------------
if menu == "Create Collection":
    st.title("Create Collection")
    st.markdown(
        "Create a named collection that points to a local folder. Use Browse to pick a folder (desktop only)."
    )

    folder_key = "create_folder_path"
    if folder_key not in st.session_state:
        st.session_state[folder_key] = str(Path.home())

    col_browse_l, col_browse_r = st.columns([4, 1])
    with col_browse_l:
        st.markdown(f"**Folder:** `{st.session_state[folder_key]}`")
    with col_browse_r:
        if st.button("Browse", key="browse_create"):
            sel = pick_folder_tk()
            if sel:
                st.session_state[folder_key] = sel
                trigger_rerun()

    with st.form("create_form"):
        name = st.text_input("Collection name", value="")
        folder_input = st.text_input(
            "Folder path (full)",
            value=st.session_state.get(folder_key, ""),
            key="create_folder_path_form",
        )
        submitted = st.form_submit_button("Save collection")
        if submitted:
            name_clean = (name or "").strip()
            path_val = st.session_state.get(
                "create_folder_path_form", st.session_state.get(folder_key, "")
            )
            if not name_clean:
                st.error("Collection name is required")
            elif not path_val or not Path(path_val).exists():
                st.error("Valid folder path required")
            else:
                collections.setdefault(name_clean, {})["path"] = str(Path(path_val))
                collections[name_clean]["watching"] = False
                collections[name_clean]["last_indexed"] = None
                save_collections(collections)
                save_chat(name_clean, [])  # initialize chat file
                st.success(f"Collection '{name_clean}' saved")
                st.session_state["selected_collection"] = name_clean
                st.session_state[folder_key] = path_val
                trigger_rerun()

# -------------------------
# Collections page
# -------------------------
elif menu == "Collections":
    st.title("Collections")
    st.markdown(
        "Saved collections. Build (index), Start/Stop Watch, Delete, or change folder path."
    )
    if not collections:
        st.info("No collections found. Create one from the Create Collection menu.")
    else:
        for cname, meta in sorted(collections.items(), key=lambda kv: kv[0].lower()):
            with st.container():
                st.markdown(
                    f"<div class='card'><div class='left-title'>{cname}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div class='small'>{meta.get('path','')}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div class='muted'>Last indexed: {meta.get('last_indexed') or 'never'} {'• Watching' if meta.get('watching') else ''}</div>",
                    unsafe_allow_html=True,
                )

                path_key = f"path_input_{cname}"
                if path_key not in st.session_state:
                    st.session_state[path_key] = meta.get("path", "")

                st.text_input(
                    "Folder path (edit)",
                    value=st.session_state.get(path_key, ""),
                    key=path_key,
                )

                col1, col2, col3 = st.columns([2, 2, 2])
                with col1:
                    if st.button("Build / Index", key=f"build_{cname}"):
                        folder = Path(collections[cname].get("path", ""))
                        if not folder.exists():
                            st.error("Folder not found; update the path first.")
                        else:
                            files = list(folder.rglob("*.*"))
                            total = 0
                            with st.spinner(f"Indexing {cname}..."):
                                for f in files:
                                    if f.suffix.lower() in [
                                        ".pdf",
                                        ".txt",
                                        ".md",
                                        ".csv",
                                        ".xlsx",
                                        ".xls",
                                    ]:
                                        try:
                                            items = ingest_file(str(f))
                                            if not items:
                                                continue
                                            docs = [c for _, c in items]
                                            metas = [{"source": s} for s, _ in items]
                                            embs = embed_texts(docs)
                                            add_documents(cname, docs, metas, embs)
                                            total += len(docs)
                                        except Exception as e:
                                            with open(
                                                os.path.join(
                                                    USER_DIR, "index_errors.log"
                                                ),
                                                "a",
                                                encoding="utf-8",
                                            ) as lf:
                                                lf.write(
                                                    f"{time.asctime()}: failed {f}: {e}\n"
                                                )
                            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                            collections[cname]["last_indexed"] = ts
                            save_collections(collections)
                            st.success(f"Indexed {total} chunks into '{cname}'")
                            trigger_rerun()

                with col2:
                    if collections[cname].get("watching"):
                        if st.button("Stop Watch", key=f"stopwatch_{cname}"):
                            stop_watching(cname)
                            st.success("Stopped watcher")
                            trigger_rerun()
                    else:
                        if st.button("Start Watch", key=f"startwatch_{cname}"):
                            new_path = st.session_state.get(
                                path_key, collections[cname].get("path", "")
                            )
                            if new_path and Path(new_path).exists():
                                collections[cname]["path"] = str(Path(new_path))
                                save_collections(collections)
                                start_watching(cname, new_path)
                                st.success("Started watcher")
                                trigger_rerun()
                            else:
                                st.error(
                                    "Valid folder path required before starting watch."
                                )
                    if st.button("Browse", key=f"browse_{cname}"):
                        sel = pick_folder_tk()
                        if sel:
                            st.session_state[path_key] = sel
                            collections[cname]["path"] = sel
                            save_collections(collections)
                            trigger_rerun()

                with col3:
                    if st.button("Delete", key=f"delete_{cname}"):
                        stop_watching(cname)
                        collections.pop(cname, None)
                        save_collections(collections)
                        cf = chat_file_for(cname)
                        try:
                            if os.path.exists(cf):
                                os.remove(cf)
                        except Exception:
                            pass
                        st.success(f"Deleted {cname}")
                        trigger_rerun()

                st.markdown("</div>", unsafe_allow_html=True)

# -------------------------
# Chat page
# -------------------------
elif menu == "Chat":
    st.title("Chat")
    st.markdown(
        "Select a collection at top; messages scroll; input stays fixed at bottom."
    )

    if not collections:
        st.info("No collections exist. Create one first.")
    else:
        names = list(collections.keys())
        default_index = 0
        if st.session_state.get("selected_collection") in names:
            default_index = names.index(st.session_state.get("selected_collection"))
        selected = st.selectbox("Select collection", names, index=default_index)
        st.session_state["selected_collection"] = selected

        if selected not in st.session_state["chat_history"]:
            st.session_state["chat_history"][selected] = load_chat(selected)

        hist = st.session_state["chat_history"][selected]

        msgs_html = "<div class='chat-messages'>"
        for m in hist:
            role = m.get("role")
            text = m.get("text", "")
            ts = m.get("ts", "")
            if role == "user":
                msgs_html += f"<div style='text-align:right;margin:8px;'><div class='msg-user'>{text}</div><div class='msg-ts'>{ts}</div></div>"
            else:
                msgs_html += f"<div style='text-align:left;margin:8px;'><div class='msg-assistant'>{text}</div><div class='msg-ts'>{ts}</div></div>"
        msgs_html += "</div>"

        st.markdown(msgs_html, unsafe_allow_html=True)

        st.markdown("<div class='chat-input-wrapper'>", unsafe_allow_html=True)
        just_sent_key = f"just_sent_{selected}"
        user_text_key = f"chat_input_{selected}"

        if st.session_state.get(just_sent_key):
            st.session_state[user_text_key] = ""
            st.session_state.pop(just_sent_key, None)

        col_inp, col_send = st.columns([10, 1])
        with col_inp:
            user_text = st.text_area("Message", key=user_text_key, height=100)
        with col_send:
            if st.button("Send", key=f"send_{selected}"):
                if (
                    not st.session_state.get(user_text_key)
                    or not st.session_state[user_text_key].strip()
                ):
                    st.error("Type a message first.")
                else:
                    user_msg = st.session_state[user_text_key].strip()
                    hist.append(
                        {
                            "role": "user",
                            "text": user_msg,
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                    )
                    save_chat(selected, hist)
                    try:
                        q_emb = embed_texts([user_msg])[0]
                        res = query(selected, q_emb, n=4)
                        docs = (
                            res.get("documents", [[]])[0]
                            if isinstance(res.get("documents", []), list)
                            else []
                        )
                        metadatas = (
                            res.get("metadatas", [[]])[0]
                            if isinstance(res.get("metadatas", []), list)
                            else []
                        )
                        context = ""
                        if docs:
                            context = "\n\n---\n\n".join(
                                [
                                    f"Source: {m.get('source')}\n{d}"
                                    for m, d in zip(metadatas, docs)
                                ]
                            )
                        answer = chat_with_context(
                            "You are a helpful assistant.", user_msg, context
                        )
                        hist.append(
                            {
                                "role": "assistant",
                                "text": answer,
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            }
                        )
                        save_chat(selected, hist)
                    except Exception as e:
                        hist.append(
                            {
                                "role": "assistant",
                                "text": f"Error: {e}",
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            }
                        )
                        save_chat(selected, hist)

                    st.session_state[just_sent_key] = True
                    trigger_rerun()

        st.markdown("</div>", unsafe_allow_html=True)
# when user chooses dashboard menu:
if menu == "Dashboard":
    show_dashboard(USER_DIR, COLLECTIONS_JSON)

elif menu == "Collection Report":
    show_collection_report_with_headlines(USER_DIR, COLLECTIONS_JSON)
# -------------------------
# Settings page (keeps sidebar visible)
# -------------------------
elif menu == "Settings":
    st.title("Settings")
    st.markdown(
        "Edit Azure OpenAI configuration and app preferences. Changes are saved to your user data directory."
    )

    # reload current cfg so editor pre-fills latest
    cfg = load_or_copy_config()
    show_settings_editor(cfg, USER_DIR)

    # small UX: show a back link to return to previous menu (default Chat)
    col_back, col_blank = st.columns([1, 9])
    with col_back:
        if st.button("Back to App"):
            st.session_state["menu_selected"] = "Chat"
            trigger_rerun()

# Footer
st.markdown("---")
st.markdown(f"Stored data: `{USER_DIR}`")
