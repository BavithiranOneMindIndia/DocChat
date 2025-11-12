# store.py
import os
from typing import List, Dict, Any
from configparser import ConfigParser
import chromadb

# import the utils loader to get config & user data dir
from utils import load_or_copy_config, get_app_user_data_dir

cfg = load_or_copy_config()
app_name = cfg.get("app", "app_name", fallback="ProDocChat")
PERSIST_DIR = cfg.get("chroma", "persist_directory", fallback="").strip()
if not PERSIST_DIR:
    PERSIST_DIR = os.path.join(get_app_user_data_dir(app_name), "chromadb")
os.makedirs(PERSIST_DIR, exist_ok=True)

# New Chroma persistent client (modern API)
client = chromadb.PersistentClient(path=PERSIST_DIR)

def get_collection(name: str):
    name = name or "default"
    try:
        return client.get_or_create_collection(name)
    except Exception as e:
        print(f"Error accessing collection '{name}': {e}")
        return client.get_or_create_collection(name)

def add_documents(collection_name: str, docs: List[str], metadatas: List[Dict[str, Any]], embeddings: List[List[float]]):
    col = get_collection(collection_name)
    # create ids that won't collide across restarts
    from uuid import uuid4
    ids = [f"{collection_name}_{uuid4().hex}" for _ in docs]
    col.add(ids=ids, documents=docs, metadatas=metadatas, embeddings=embeddings)

def query(collection_name: str, query_emb: List[float], n=4):
    col = get_collection(collection_name)
    results = col.query(query_embeddings=[query_emb], n_results=n, include=['documents', 'metadatas', 'distances'])
    return results

def list_collections():
    try:
        return client.list_collections()
    except Exception:
        return []
