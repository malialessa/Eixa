import logging
from google.cloud import firestore
from firestore_client_singleton import _initialize_firestore_client_instance

from config import (
    TOP_LEVEL_COLLECTIONS_MAP,
    SUBCOLLECTIONS_MAP,
    USERS_COLLECTION,
)

logger = logging.getLogger(__name__)

def get_top_level_collection(logical_name: str) -> firestore.CollectionReference:
    db = _initialize_firestore_client_instance()
    key = logical_name.lower()
    collection_real_name = TOP_LEVEL_COLLECTIONS_MAP.get(key)
    if not collection_real_name:
        logger.error(f"COLLECTIONS_MANAGER | Top-level logical collection name '{logical_name}' not found in map.") # Novo log
        raise KeyError(f"Nome lógico de coleção top-level '{logical_name}' não encontrado em TOP_LEVEL_COLLECTIONS_MAP. Nomes válidos: {list(TOP_LEVEL_COLLECTIONS_MAP.keys())}")
    logger.debug(f"COLLECTIONS_MANAGER | Resolved top-level collection '{logical_name}' to real name '{collection_real_name}'.") # Novo log
    return db.collection(collection_real_name)

# REVISADO: Esta função APENAS retorna a referência do documento do usuário.
# A lógica de verificar a existência e criar o placeholder foi movida para o orchestrator.
def get_user_doc_ref(user_id: str) -> firestore.DocumentReference:
    db = _initialize_firestore_client_instance()
    # Continua usando USERS_COLLECTION, que aponta para o nome real 'eixa_users'
    user_doc_ref = db.collection(USERS_COLLECTION).document(user_id)
    logger.debug(f"COLLECTIONS_MANAGER | Getting user document reference for user_id: '{user_id}' in collection: '{USERS_COLLECTION}'. Full path: {user_doc_ref.path}") # Novo log
    return user_doc_ref

def get_user_subcollection(user_id: str, logical_subcollection_name: str) -> firestore.CollectionReference:
    # Esta função agora confia que o documento pai (usuário) já foi criado
    # pelo orchestrator antes de ser chamada.
    user_doc_ref = get_user_doc_ref(user_id)

    real_name = SUBCOLLECTIONS_MAP.get(logical_subcollection_name.lower())
    if not real_name:
        logger.error(f"COLLECTIONS_MANAGER | Subcollection logical name '{logical_subcollection_name}' not found in map.") # Novo log
        raise KeyError(f"Subcoleção '{logical_subcollection_name}' não encontrada em SUBCOLLECTIONS_MAP. Nomes válidos: {list(SUBCOLLECTIONS_MAP.keys())}")

    subcollection_ref = user_doc_ref.collection(real_name)
    # LINHA MODIFICADA AQUI: subcollection_ref.path -> f"{subcollection_ref.parent.path}/{subcollection_ref.id}"
    logger.debug(f"COLLECTIONS_MANAGER | Getting subcollection '{logical_subcollection_name}' as '{real_name}' under user document '{user_id}'. Full path: {subcollection_ref.parent.path}/{subcollection_ref.id}") # NOVO LOG CORRIGIDO
    return subcollection_ref

def get_task_doc_ref(user_id: str, date_str: str) -> firestore.DocumentReference:
    task_doc_ref = get_user_subcollection(user_id, 'agenda').document(date_str)
    logger.debug(f"COLLECTIONS_MANAGER | Getting task doc ref for user '{user_id}' on date '{date_str}'. Full path: {task_doc_ref.path}") # Novo log
    return task_doc_ref

def get_project_doc_ref(user_id: str, project_id: str) -> firestore.DocumentReference:
    project_doc_ref = get_user_subcollection(user_id, 'projects').document(project_id)
    logger.debug(f"COLLECTIONS_MANAGER | Getting project doc ref for user '{user_id}' project '{project_id}'. Full path: {project_doc_ref.path}") # Novo log
    return project_doc_ref

def get_unscheduled_tasks_collection(user_id: str) -> firestore.CollectionReference:
    collection_ref = get_user_subcollection(user_id, 'unscheduled')
    logger.debug(f"COLLECTIONS_MANAGER | Getting unscheduled tasks collection for user '{user_id}'. Full path: {collection_ref.parent.path}/{collection_ref.id}")
    return collection_ref

def get_unscheduled_task_doc_ref(user_id: str, task_id: str) -> firestore.DocumentReference:
    doc_ref = get_unscheduled_tasks_collection(user_id).document(task_id)
    logger.debug(f"COLLECTIONS_MANAGER | Getting unscheduled task doc ref for user '{user_id}' task '{task_id}'. Full path: {doc_ref.path}")
    return doc_ref

def get_vector_memory_doc_ref(user_id: str, memory_id: str) -> firestore.DocumentReference:
    vector_memory_doc_ref = get_user_subcollection(user_id, 'vector_memory').document(memory_id)
    logger.debug(f"COLLECTIONS_MANAGER | Getting vector memory doc ref for user '{user_id}' memory '{memory_id}'. Full path: {vector_memory_doc_ref.path}") # Novo log
    return vector_memory_doc_ref
