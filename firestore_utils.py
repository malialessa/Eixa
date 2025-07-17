import logging
from google.cloud import firestore
from firestore_client_singleton import _initialize_firestore_client_instance
from collections_manager import get_top_level_collection, get_user_doc_ref
import copy
from datetime import datetime, timezone
import asyncio # Mantenha asyncio importado para asyncio.to_thread

logger = logging.getLogger(__name__)

async def get_firestore_document_data(logical_collection_name: str, document_id: str) -> dict | None:
    try:
        collection_ref = get_top_level_collection(logical_collection_name)
        db = _initialize_firestore_client_instance()
        doc = await asyncio.to_thread(db.collection(collection_ref.id).document(document_id).get)
        if doc.exists:
            logger.debug(f"Document '{document_id}' fetched from collection '{logical_collection_name}'.")
            return doc.to_dict()
        else:
            logger.info(f"Document '{document_id}' not found in collection '{logical_collection_name}'.")
            return None
    except Exception as e:
        logger.error(f"Error fetching document '{document_id}' from collection '{logical_collection_name}': {e}", exc_info=True)
        return None

async def set_firestore_document(logical_collection_name: str, document_id: str, data: dict, merge: bool = False):
    try:
        collection_ref = get_top_level_collection(logical_collection_name)
        db = _initialize_firestore_client_instance()
        await asyncio.to_thread(db.collection(collection_ref.id).document(document_id).set, data, merge=merge)
        logger.info(f"Document '{document_id}' set in collection '{logical_collection_name}'. Merge: {merge}")
    except Exception as e:
        logger.error(f"Error setting document '{document_id}' in collection '{logical_collection_name}': {e}", exc_info=True)
        raise

async def delete_firestore_document(logical_collection_name: str, document_id: str):
    try:
        collection_ref = get_top_level_collection(logical_collection_name)
        db = _initialize_firestore_client_instance()
        await asyncio.to_thread(db.collection(collection_ref.id).document(document_id).delete)
        logger.info(f"Document '{document_id}' deleted from collection '{logical_collection_name}'.")
    except Exception as e:
        logger.error(f"Error deleting document '{document_id}' from collection '{logical_collection_name}': {e}", exc_info=True)
        raise

# NOVA FUNÇÃO AUXILIAR: Normaliza a estrutura de goals
def _normalize_goals_structure(goals_data: dict) -> dict:
    """
    Normaliza a estrutura da seção 'goals' para garantir que sejam listas de dicionários
    com a chave 'value'.
    """
    normalized_goals = {}
    for term_type in ['short_term', 'medium_term', 'long_term']:
        items = goals_data.get(term_type, [])
        if isinstance(items, list):
            normalized_items = []
            for item in items:
                if isinstance(item, str):
                    normalized_items.append({"value": item})
                elif isinstance(item, dict) and "value" in item:
                    normalized_items.append(item)
                else:
                    # Se for um dicionário mas não tiver 'value', tenta usar o primeiro valor
                    if isinstance(item, dict) and item:
                        normalized_items.append({"value": str(list(item.values())[0])})
                    else:
                        logger.warning(f"Unexpected goal item format for {term_type}: {item}. Skipping.")
            normalized_goals[term_type] = normalized_items
        else:
            logger.warning(f"Goals '{term_type}' is not a list. Skipping normalization.")
            normalized_goals[term_type] = []
    return normalized_goals


async def get_user_profile_data(user_id: str, user_profile_template_content: dict) -> dict:
    # Esta função agora vai interagir com a coleção 'eixa_profiles' (após a mudança no config.py)
    profile_collection_ref = get_top_level_collection('profiles') # Usa 'profiles' -> 'eixa_profiles'

    db = _initialize_firestore_client_instance()
    profile_doc = await asyncio.to_thread(db.collection(profile_collection_ref.id).document(user_id).get)

    if profile_doc.exists:
        logger.info(f"User profile for '{user_id}' fetched from Firestore in '{profile_collection_ref.id}'.")
        current_profile_data = profile_doc.to_dict().get('user_profile', profile_doc.to_dict())

        # NOVO: Normaliza a estrutura de 'goals' APÓS CARREGAR do Firestore
        if 'goals' in current_profile_data and isinstance(current_profile_data['goals'], dict):
            current_profile_data['goals'] = _normalize_goals_structure(current_profile_data['goals'])
            # Opcional: Salvar o perfil normalizado de volta no Firestore para migração persistente
            # await set_firestore_document('profiles', user_id, {'user_profile': current_profile_data}, merge=False)
            # logger.info(f"User profile for '{user_id}' normalized and updated in Firestore.")

        return current_profile_data
    else:
        logger.info(f"User profile for '{user_id}' not found. Creating default profile.")

        if not isinstance(user_profile_template_content, dict):
            logger.error("Default template para perfil de usuário é inválido (não é um dicionário). Retornando vazio.")
            return {}

        new_profile_content = copy.deepcopy(user_profile_template_content)

        # Garante que 'creation_date' só é definida na primeira criação
        if "creation_date" not in new_profile_content or new_profile_content["creation_date"] is None:
            new_profile_content["creation_date"] = datetime.now(timezone.utc).isoformat()

        # 'last_updated' sempre é atualizado na criação/atualização
        new_profile_content["last_updated"] = datetime.now(timezone.utc).isoformat()

        new_profile_content["user_id"] = user_id # Atribui o user_id

        if not new_profile_content.get('name'):
             new_profile_content['name'] = user_id

        # NOVO: Normaliza a estrutura de 'goals' ANTES DE SALVAR o novo perfil (garante template consistente)
        if 'goals' in new_profile_content and isinstance(new_profile_content['goals'], dict):
            new_profile_content['goals'] = _normalize_goals_structure(new_profile_content['goals'])

        try:
            # Salva o perfil completo dentro de uma sub-chave 'user_profile'
            await asyncio.to_thread(db.collection(profile_collection_ref.id).document(user_id).set, {'user_profile': new_profile_content})
            logger.info(f"Default user profile created and saved for '{user_id}' in '{profile_collection_ref.id}'.")
            return new_profile_content
        except Exception as e:
            logger.error(f"Falha ao criar perfil padrão para o usuário '{user_id}': {e}", exc_info=True)
            return new_profile_content

async def save_interaction(user_id: str, user_input: str, eixa_output: str, language: str, logical_collection_name: str):
    try:
        interactions_ref = get_top_level_collection(logical_collection_name)
        timestamp = datetime.now(timezone.utc)
        doc_id = f"{user_id}_{timestamp.isoformat().replace(':', '-').replace('.', '_')}"

        interaction_data = {
            "user_id": user_id,
            "input": user_input,
            "output": eixa_output,
            "language": language,
            "timestamp": timestamp
        }
        db = _initialize_firestore_client_instance()
        await asyncio.to_thread(db.collection(interactions_ref.id).document(doc_id).set, interaction_data)
        logger.info(f"Interaction saved for user '{user_id}' with ID '{doc_id}'.")
    except Exception as e:
        logger.error(f"Error saving interaction for user '{user_id}': {e}", exc_info=True)