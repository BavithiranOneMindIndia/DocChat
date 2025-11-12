# watcher.py
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time
import threading
from ingest import ingest_file
from store import add_documents
from azure_client import embed_texts




class RebuildHandler(FileSystemEventHandler):
    def __init__(self, collection_name):
        self.collection = collection_name


    def on_modified(self, event):
        if event.is_directory:
            return
        self.process(event.src_path)


    def on_created(self, event):
        if event.is_directory:
            return
        self.process(event.src_path)


    def process(self, path):
        print("Processing change:", path)
        items = ingest_file(path)
        docs = [c for _, c in items]
        metadatas = [{"source": p} for p, _ in items]
        embs = embed_texts(docs)
        add_documents(self.collection, docs, metadatas, embs)




def start_watch(path, collection_name):
    event_handler = RebuildHandler(collection_name)
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()