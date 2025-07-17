import datetime
import logging
import os
import subprocess
import json 
from google.cloud import firestore 
from google.cloud import storage 

from firestore_client_singleton import _initialize_firestore_client_instance
from collections_manager import get_top_level_collection

logger = logging.getLogger(__name__)

def export_firestore_to_gcs(project_id: str, bucket_name: str, collections_logical_names: list[str] = None):
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_uri_prefix = f"gs://{bucket_name}/eixa_backup/firestore_managed/{timestamp_str}"

    real_collection_names = []
    if collections_logical_names:
        for logical_name in collections_logical_names:
            try:
                collection_ref = get_top_level_collection(logical_name)
                real_collection_names.append(collection_ref.id)
            except KeyError:
                logger.warning(f"Logical collection name '{logical_name}' not found in config. Skipping for export.")

    command = [
        "gcloud", "firestore", "export",
        output_uri_prefix,
        f"--project={project_id}"
    ]

    if real_collection_names:
        command.append(f"--collection-ids={','.join(real_collection_names)}")

    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        logger.info(f"Firestore export initiated successfully: {result.stdout}")
        logger.info(f"Export errors (if any): {result.stderr}")
        return {"status": "success", "output_uri_prefix": output_uri_prefix, "logs": result.stdout}
    except subprocess.CalledProcessError as e:
        logger.error(f"Firestore export failed for project '{project_id}': {e.stderr}", exc_info=True)
        return {"status": "error", "message": f"Export failed: {e.stderr}"}
    except Exception as e:
        logger.error(f"Unexpected error during Firestore export: {e}", exc_info=True)
        return {"status": "error", "message": f"Unexpected error: {e}"}

def export_vectorstore_to_jsonl(project_id: str, bucket_name: str, embeddings_collection_logical_name: str = 'embeddings'): 
    db = _initialize_firestore_client_instance() 
    
    collection_ref = get_top_level_collection(embeddings_collection_logical_name)
    
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"eixa_backup/vectors/{timestamp_str}_embeddings.jsonl"
    blob_path = f"{filename}"

    try:
        storage_client = storage.Client(project=project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        with blob.open("w") as f:
            for doc in collection_ref.stream():
                data = doc.to_dict()
                f.write(json.dumps(data, default=str) + "\n")

        logger.info(f"Vector store '{embeddings_collection_logical_name}' exported to gs://{bucket_name}/{blob_path}")
        return {"status": "success", "file_path": f"gs://{bucket_name}/{blob_path}"}
    except Exception as e:
        logger.error(f"Failed to export vector store '{embeddings_collection_logical_name}': {e}", exc_info=True)
        return {"status": "error", "message": f"Export failed: {e}"}
