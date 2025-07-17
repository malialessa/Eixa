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
        raise KeyError(f"Nome lógico de coleção top-level '{logical_name}' não encontrado em TOP_LEVEL_COLLECTIONS_MAP. Nomes válidos: {list(TOP_LEVEL_COLLECTIONS_MAP.keys())}")
    return db.collection(collection_real_name)

# REVISADO: Esta função APENAS retorna a referência do documento do usuário.
# A lógica de verificar a existência e criar o placeholder foi movida para o orchestrator.
def get_user_doc_ref(user_id: str) -> firestore.DocumentReference:
    db = _initialize_firestore_client_instance()
    # Continua usando USERS_COLLECTION, que aponta para o nome real 'eixa_users'
    user_doc_ref = db.collection(USERS_COLLECTION).document(user_id)
    logger.debug(f"Getting user document reference for user_id: {user_id} in collection: {USERS_COLLECTION}")
    return user_doc_ref

def get_user_subcollection(user_id: str, logical_subcollection_name: str) -> firestore.CollectionReference:
    # Esta função agora confia que o documento pai (usuário) já foi criado
    # pelo orchestrator antes de ser chamada.
    user_doc_ref = get_user_doc_ref(user_id)

    real_name = SUBCOLLECTIONS_MAP.get(logical_subcollection_name.lower())
    if not real_name:
        raise KeyError(f"Subcoleção '{logical_subcollection_name}' não encontrada em SUBCOLLECTIONS_MAP. Nomes válidos: {list(SUBCOLLECTIONS_MAP.keys())}")

    logger.debug(f"Getting subcollection '{real_name}' under user document '{user_id}'.")
    return user_doc_ref.collection(real_name)

def get_task_doc_ref(user_id: str, date_str: str) -> firestore.DocumentReference:
    return get_user_subcollection(user_id, 'agenda').document(date_str)

def get_project_doc_ref(user_id: str, project_id: str) -> firestore.DocumentReference:
    return get_user_subcollection(user_id, 'projects').document(project_id)

def get_vector_memory_doc_ref(user_id: str, memory_id: str) -> firestore.DocumentReference:
    return get_user_subcollection(user_id, 'vector_memory').document(memory_id)