import asyncio
import hashlib
import inspect
import logging
import magic
import os
import mimetypes
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import time as datetime_time
from logging.handlers import TimedRotatingFileHandler
from meilisearch_python_sdk import AsyncClient, Client
from multiprocessing import Process
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import tika_module
import whisper_module

modules = [tika_module, whisper_module]

# Ensure the data directory exists
if not os.path.exists("./data/logs"):
    os.makedirs("./data/logs")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        TimedRotatingFileHandler(
            "./data/logs/indexer.log",
            when="midnight",
            interval=1,
            backupCount=7,
            atTime=datetime_time(2, 30)
        ),
        logging.StreamHandler(),
    ],
)

other_loggers = ['httpx', 'tika.tika', 'faster_whisper', 'watchdog'] 
for logger_name in other_loggers:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(f"./data/logs/{logger_name}.log")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False 

DIRECTORY_TO_INDEX = os.environ.get("DIRECTORY_TO_INDEX", "/data")
MEILISEARCH_HOST = os.environ.get("MEILISEARCH_HOST", "http://meilisearch:7700")
INDEX_NAME = os.environ.get("INDEX_NAME", "files")
DOMAIN = os.environ.get("DOMAIN", "private.0819870.xyz")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "10000"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "32"))
SLEEP_BETWEEN_MODULE_RUNS = int(os.environ.get("SLEEP_BETWEEN_MODULE_RUNS", "300"))
ALLOWED_TIME_PER_MODULE = int(os.environ.get("ALLOW_TIME_PER_MODULE", "900"))

client = None
index = None

async def init_meili():
    global client, index
    client = AsyncClient(MEILISEARCH_HOST)
    try:
        index = await client.get_index(INDEX_NAME)
    except Exception as e:
        if getattr(e, 'code', None) == "index_not_found":
            try:
                logging.info(f"Creating MeiliSearch index '{INDEX_NAME}'.")
                index = await client.create_index(INDEX_NAME, primary_key="id")
            except Exception as create_e:
                logging.error(f"Failed to create MeiliSearch index '{INDEX_NAME}': {create_e.message}")
                raise
        else:
            logging.error(f"Failed to initialize MeiliSearch: {e.message}")
            raise

    filterable_attributes = ["id"] + [module.FIELD_NAME for module in modules]
    
    try:
        await index.update_filterable_attributes(filterable_attributes)
        await index.update_sortable_attributes(["mtime"])
    except Exception as attr_e:
        logging.error(f"Failed to update index attributes: {attr_e}")
        raise

async def get_doc_count_meili():
    if not index:
        raise Exception("MeiliSearch index is not initialized.")
    
    try:
        stats = await index.get_stats()
        return stats.number_of_documents
    except Exception as e:
        logging.error(f"Failed to get MeiliSearch index stats: {e}")
        raise

async def add_or_update_doc_meili(doc, wait_for_task=False):
    if not index:
        raise Exception("MeiliSearch index is not initialized.")
    
    if doc:
        try:
            task = await index.update_documents([doc])
            if wait_for_task:
                await wait_for_task_completion_meili(task)
        except Exception as e:
            logging.error(f"Failed to add or update MeiliSearch document: {e}")
            raise

async def add_or_update_docs_meili(docs, wait_for_task=False):
    if not index:
        raise Exception("MeiliSearch index is not initialized.")
    
    if docs:
        try:
            for i in range(0, len(docs), BATCH_SIZE):
                batch = docs[i:i + BATCH_SIZE]
                task = await index.update_documents(batch)
                if wait_for_task:
                    await wait_for_task_completion_meili(task)
        except Exception as e:
            logging.error(f"Failed to add/update MeiliSearch documents: {e}")
            raise

async def delete_docs_by_id_meili(ids, wait_for_task=False):
    if not index:
        raise Exception("MeiliSearch index is not initialized.")
    
    try:
        if ids:
            for i in range(0, len(ids), BATCH_SIZE):
                batch = ids[i:i + BATCH_SIZE]
                task = await index.delete_documents(ids=batch)
                if wait_for_task:
                    await wait_for_task_completion_meili(task)
    except Exception as e:
        logging.error(f"Failed to delete MeiliSearch documents by ID: {e}")
        raise

async def get_doc_meili(doc_id):
    if not index:
        raise Exception("MeiliSearch index is not initialized.")
    
    try:
        doc = await index.get_document(doc_id)
        return doc
    except Exception as e:
        logging.error(f"Failed to get MeiliSearch document with ID {doc_id}: {e}")
        raise

async def get_all_docs_meili():
    if not index:
        raise Exception("MeiliSearch index is not initialized.")
    
    docs = []
    offset = 0
    limit = BATCH_SIZE
    try:
        while True:
            result = await index.get_documents(offset=offset, limit=limit)
            docs.extend(result.results)
            if len(result.results) < limit:
                break
            offset += limit
        return docs
    except Exception as e:
        logging.error(f"Failed to retrieve all MeiliSearch documents: {e}")
        raise

async def get_all_pending_jobs(module):
    if not index:
        raise Exception("MeiliSearch index is not initialized.")
    
    docs = []
    offset = 0
    limit = BATCH_SIZE
    filter_query = f'{module.FIELD_NAME} < {module.VERSION}'
    fields = ["url", "mtime", "type"] + module.DATA_FIELD_NAMES
    
    try:
        while True:
            response = await index.get_documents(
                filter=filter_query,
                limit=limit,
                offset=offset,
                fields=fields
            )
            docs.extend(response.results)
            if len(response.results) < limit:
                break
            offset += limit
        return docs
    except Exception as e:
        logging.error(f"Failed to get pending jobs from MeiliSearch: {e}")
        raise

async def wait_for_task_completion_meili(task_info):
    if not client:
        raise Exception("MeiliSearch client is not initialized.")
    
    try:
        while True:
            task = await client.get_task(task_info.task_uid)
            if task.status == 'succeeded':
                return
            elif task.status == 'failed':
                raise Exception("MeiliSearch task failed!")
            await asyncio.sleep(0.5)
    except Exception as e:
        logging.error(f"Error while waiting for task completion: {e}")
        raise

def get_mime_magic(file_path):
    mime = magic.Magic(mime=True)
    mime_type = mime.from_file(file_path)
    
    if mime_type == 'application/octet-stream':
        mime_type, _ = mimetypes.guess_type(file_path)
        
        if mime_type is None:
            mime_type = 'application/octet-stream'
    
    return mime_type

def get_meili_id_from_relative_path(relative_path):
    return hashlib.sha256(relative_path.encode()).hexdigest()

def get_meili_id_from_file_path(file_path):
    return get_meili_id_from_relative_path(get_relative_path_from_file_path(file_path))

def get_file_path_from_meili_doc(doc):
    return Path(doc['url'].replace(f"https://{DOMAIN}/", f"{DIRECTORY_TO_INDEX}/")).as_posix()

def get_relative_path_from_meili_doc(doc):
    return Path(doc['url'].replace(f"https://{DOMAIN}/", "")).as_posix()

def get_url_from_relative_path(relative_path):
    return f"https://{DOMAIN}/{relative_path}"

def get_relative_path_from_file_path(file_path):
    return Path(file_path).relative_to(DIRECTORY_TO_INDEX).as_posix()

async def sync_meili_docs():
    logging.info("sync meili docs with files")

    all_docs = await get_all_docs_meili()
    existing_meili_file_paths = set()
    existing_docs = {}
    
    logging.info(f"{len(all_docs)} meili docs exist")

    for doc in all_docs:
        file_path = get_file_path_from_meili_doc(doc)
        existing_meili_file_paths.add(file_path)
        existing_docs[doc["id"]] = doc

    directories_to_scan = [DIRECTORY_TO_INDEX]
    updated = []
    exists = set()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []

        while directories_to_scan:
            dir_path = directories_to_scan.pop()
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False):
                            directories_to_scan.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            futures.append(executor.submit(create_meili_doc_from_file_path, entry.path, existing_docs))
            except Exception as e:
                logging.exception(f'failed to scan directory "{dir_path}": {e}')

        for future in as_completed(futures):
            result = future.result()
            if result:
                file_path, document, status = result
                updated.append(document)
                if status != "modified":
                    exists.add(file_path)

    deleted = existing_meili_file_paths - exists
    deleted_meili_ids = [get_meili_id_from_file_path(file_path) for file_path in deleted]

    await delete_docs_by_id_meili(deleted_meili_ids, wait_for_task=True),
    await add_or_update_docs_meili(updated, wait_for_task=True)
    
    doc_count = await get_doc_count_meili()
    
    logging.info(f"{len(deleted_meili_ids)} meili docs deleted")
    logging.info(f"{len(updated)} meili docs created/updated")
    logging.info(f"{doc_count} meili docs now exist")

def create_meili_doc_from_file_path(file_path, existing_docs = {}):
    try:
        path = Path(file_path)
        relative_path = path.relative_to(DIRECTORY_TO_INDEX).as_posix()
        stat = path.stat()

        current_mtime = stat.st_mtime

        mime = get_mime_magic(path)
        doc_id = get_meili_id_from_relative_path(relative_path)
        
        status = "new"    
        if doc_id in existing_docs:
            if existing_docs[doc_id]["mtime"] < current_mtime:
                status = "modified"
            else:
                status = "same"

        document = {
            "id": doc_id,
            "name": path.name,
            "size": stat.st_size,
            "mtime": current_mtime,
            "ctime": stat.st_ctime,
            "url": get_url_from_relative_path(relative_path),
            "type": mime,
        }

        for module in modules:
            if module.does_support_mime(mime):
                if status == "modified" or status == "new":
                    document[module.FIELD_NAME] = 0

        return (file_path, document, status)
    except Exception as e:
        logging.exception(f'failed to create meili doc for "{file_path}": {e}')
        return None

async def augment_meili_docs(module):
    try:
        pending_jobs = await get_all_pending_jobs(module)
        file_paths_with_mime = [
            [get_file_path_from_meili_doc(doc), doc]
            for doc in sorted(pending_jobs, key=lambda x: x['mtime'], reverse=True)
        ]
    except Exception as e:
        logging.exception(f"{module.NAME} failed to get pending: {e}")
        return

    if not file_paths_with_mime:
        return

    logging.info(f"start {module.NAME} for {len(file_paths_with_mime)}")

    try:
        logging.info(f"init {module.NAME}")
        await module.init()
    except Exception as e:
        logging.exception(f"failed to init {module.NAME}: {e}")
        return

    try:
        semaphore = asyncio.Semaphore(module.MAX_WORKERS)

        async def sem_task(fp):
            async with semaphore:
                return await augment_meili_doc_from_file_path(fp[0], fp[1], module)

        start_time = time.time()
        tasks = [asyncio.create_task(sem_task(fp)) for fp in file_paths_with_mime]
        
        for fut in asyncio.as_completed(tasks):
            try:
                await fut        
                elapsed_time = time.time() - start_time
                if elapsed_time > ALLOWED_TIME_PER_MODULE:
                    logging.info(f"{module.NAME} yielding time to other modules")
                    for task in tasks:
                        task.cancel()
                    break
            except Exception as e:
                logging.exception(f"{module.NAME} file failed:", exc_info=e)
        
        success_count = 0
        failure_count = 0
        not_found_count = 0
        postponed_count = 0
        
        for task in tasks:
            try:
                result = await task
                if result == 0:
                    success_count += 1
                elif result == 1:
                    failure_count += 1
                elif result == 2:
                    not_found_count += 1
                elif result == 3:
                    postponed_count += 1
            except asyncio.CancelledError:
                postponed_count += 1
            except Exception as e:
                failure_count += 1
            
        logging.info(
            f"{module.NAME}: success={success_count}, fail={failure_count}, not_found={not_found_count}, postponed={postponed_count}"
        )
    except Exception as e:
        logging.exception(f'{module.NAME} batch failed: {e}')
        return

    try:
        logging.info(f"cleanup {module.NAME}")
        await module.cleanup()
    except Exception as e:
        logging.exception(f"{module.NAME} failed to cleanup: {e}")
        return

async def augment_meili_doc_from_file_path(file_path, doc, module):    
    path = Path(file_path)
    relative_path = path.relative_to(DIRECTORY_TO_INDEX).as_posix()

    logging.info(f'{module.NAME} trying "{relative_path}"')

    if not path.exists():
        logging.error(f'{module.NAME} failed "{relative_path}": not found')
        return 2

    start_time = time.monotonic()
    time_limit = ALLOWED_TIME_PER_MODULE

    try:
        async for fields in module.get_fields(file_path, doc):
            if not path.exists():
                logging.error(f'{module.NAME} failed "{relative_path}": not found after processing')
                return 2
            
            current_time = time.monotonic()
            elapsed_time = current_time - start_time
            
            try:
                updated_doc = {
                    "id": get_meili_id_from_relative_path(relative_path),
                    **fields,
                }
                
                await add_or_update_doc_meili(updated_doc)
            except Exception as e:
                logging.exception(f'{module.NAME} failed to update "{relative_path}": {e}')
                return 1
            
            if elapsed_time > time_limit:
                logging.info(f'{module.NAME} postponing "{relative_path}"')
                return 3
        
        try:
            updated_doc = {
                "id": get_meili_id_from_relative_path(relative_path),
                f'{module.FIELD_NAME}': module.VERSION,
            }
            
            await add_or_update_doc_meili(updated_doc)
        except Exception as e:
            logging.exception(f'{module.NAME} failed to update "{relative_path}": {e}')
            return 1    
        
        return 0
    except Exception as e:
        logging.exception(f'{module.NAME} failed "{relative_path}": {e}')
        return 1
     
class EventHandler(FileSystemEventHandler):
    def __init__(self):
        self.meili_client = Client(MEILISEARCH_HOST)
        self.index = self.meili_client.index(INDEX_NAME)

    def on_created(self, event):
        if not event.is_directory:
            try:
                logging.info(f'created "{event.src_path}"')
                self.create_or_update_meili(event.src_path)
            except Exception as e:
                logging.exception(f'failed to handle created event for "{event.src_path}": {e}')

    def on_modified(self, event):
        if not event.is_directory:
            try:
                logging.info(f'modified "{event.src_path}"')
                self.delete_meili(event.src_path)
                self.create_or_update_meili(event.src_path)
            except Exception as e:
                logging.exception(f'failed to handle modified event for "{event.src_path}": {e}')

    def on_deleted(self, event):
        if not event.is_directory:
            try:
                logging.info(f'deleted "{event.src_path}"')
                self.delete_meili(event.src_path)
            except Exception as e:
                logging.exception(f'failed to handle deleted event for "{event.src_path}": {e}')

    def on_moved(self, event):
        if not event.is_directory:
            try:
                logging.info(f'moved "{event.src_path}" to "{event.dest_path}"')
                self.delete_meili(event.src_path)
                self.delete_meili(event.dest_path)
                self.create_or_update_meili(event.dest_path)
            except Exception as e:
                logging.exception(f'failed to handle moved event from "{event.src_path}" to "{event.dest_path}": {e}')

    def create_or_update_meili(self, file_path):
        try:
            fp, document, status = create_meili_doc_from_file_path(file_path)
            self.index.add_documents([document])
            logging.info(f'Added/Updated "{file_path}" in index')
        except Exception as e:
            logging.exception(f'Failed to handle file "{file_path}": {e}')
    
    def delete_meili(self, file_path):
        try:
            id = get_meili_id_from_file_path(file_path)
            self.index.delete_document(id)
            logging.info(f'Deleted "{file_path}" from index')
        except Exception as e:
            logging.exception(f'Failed to delete file "{file_path}": {e}')
        
def process_main():
    logger = logging.getLogger('watchdog')
    logging.root = logger
    logging.getLogger().handlers = logger.handlers

    try:
        logging.info(f'file move watchdog process started')
        observer = Observer()
        handler = EventHandler()
        observer.schedule(handler, DIRECTORY_TO_INDEX, recursive=True)
        observer.start()
        observer.join()
    except Exception as e:
        logging.exception(f'failed to start the watchdog observer: {e}')
    
async def update_meili_docs():
    await sync_meili_docs()
    
    logging.info(f'start file move watchdog process')
    process = Process(target=process_main)
    process.start()    
    
    while True:
        for module in modules:
            await augment_meili_docs(module)
        logging.info(f'waiting for next module cycle')
        await asyncio.sleep(SLEEP_BETWEEN_MODULE_RUNS)
           
async def main():
    await init_meili()
    await update_meili_docs()

if __name__ == "__main__":
    asyncio.run(main())