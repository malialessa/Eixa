import logging
import uuid
from google.cloud import firestore
import asyncio

from firestore_client_singleton import _initialize_firestore_client_instance
from config import USERS_COLLECTION, EIXA_INTERACTIONS_COLLECTION
from collections_manager import get_user_subcollection, get_task_doc_ref, get_project_doc_ref, get_top_level_collection

logger = logging.getLogger(__name__)

# --- Funções de Access to Data Agenda (Daily Tasks) ---

async def get_daily_tasks_data(user_id: str, date_str: str) -> dict:
    doc_ref = get_task_doc_ref(user_id, date_str)
    # Correção: Logar .path apenas para DocumentReference
    logger.debug(f"EIXA_DATA | Getting daily tasks for user '{user_id}' on date '{date_str}'. Doc path: {doc_ref.path}")
    doc = await asyncio.to_thread(doc_ref.get)

    if not doc.exists:
        logger.info(f"EIXA_DATA | No daily tasks document found for user '{user_id}' on '{date_str}'. Returning empty list.")
        return {"tasks": []}

    data = doc.to_dict()
    logger.debug(f"EIXA_DATA | Raw data fetched for daily tasks for '{user_id}' on '{date_str}': {data}")

    if "tasks" in data and isinstance(data["tasks"], list):
        modern_tasks = []
        for t in data["tasks"]:
            if isinstance(t, str):
                modern_tasks.append({"id": str(uuid.uuid4()), "description": t, "completed": False})
                logger.warning(f"EIXA_DATA | Converted old string task format for '{user_id}' on '{date_str}': '{t}'.")
            elif isinstance(t, dict):
                t.setdefault("id", str(uuid.uuid4()))
                t.setdefault("description", "Tarefa sem descrição")
                t.setdefault("completed", False)
                modern_tasks.append(t)
            else:
                logger.critical(f"EIXA_DATA | UNEXPECTED TASK FORMAT. Task '{t}' for user '{user_id}' on '{date_str}' is neither string nor dict. Skipping.", exc_info=True)
        data["tasks"] = modern_tasks
    else:
        logger.critical(f"EIXA_DATA | CRITICAL: Document for '{user_id}' on '{date_str}' does NOT contain a 'tasks' list or 'tasks' field is missing. Data: {data}. Initializing 'tasks' as empty.", exc_info=True)
        data["tasks"] = []
    
    logger.debug(f"EIXA_DATA | Processed daily tasks data for '{user_id}' on '{date_str}': {data}")
    return data

async def save_daily_tasks_data(user_id: str, date_str: str, data: dict):
    doc_ref = get_task_doc_ref(user_id, date_str)
    logger.debug(f"EIXA_DATA | Attempting to save daily tasks for user '{user_id}' on '{date_str}'. Doc path: {doc_ref.path}. Data: {data}")
    try:
        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | Daily tasks for user '{user_id}' on '{date_str}' saved to Firestore successfully.")
    except Exception as e:
        # AQUI É A MUDANÇA: Log Critical Error e raise para que o erro seja propagado
        logger.critical(f"EIXA_DATA | CRITICAL ERROR: Failed to save daily tasks to Firestore for user '{user_id}' on '{date_str}'. Doc Path: {doc_ref.path}. Payload: {data}. Error: {e}", exc_info=True)
        raise # Re-raise a exceção para que o orchestrator saiba que falhou

async def get_all_daily_tasks(user_id: str) -> dict:
    agenda_ref = get_user_subcollection(user_id, 'agenda')
    # CORREÇÃO: Usar .id para CollectionReference (não tem .path)
    logger.debug(f"EIXA_DATA | Attempting to retrieve all daily tasks for user '{user_id}' from collection ID: {agenda_ref.id}. Full path: {agenda_ref.parent.id}/{agenda_ref.id}") # Adicionado full path para debug
    all_tasks = {}
    try:
        docs = await asyncio.to_thread(lambda: list(agenda_ref.stream()))
        if not docs:
            logger.info(f"EIXA_DATA | No daily task documents found for user '{user_id}'.")
        for doc_snapshot in docs:
            date_str = doc_snapshot.id
            all_tasks[date_str] = await get_daily_tasks_data(user_id, date_str)
            logger.debug(f"EIXA_DATA | Retrieved doc '{date_str}' for user '{user_id}'.")
        logger.info(f"EIXA_DATA | Retrieved all daily tasks for user '{user_id}'. Total days: {len(all_tasks)}")
    except Exception as e:
        logger.error(f"EIXA_DATA | Error retrieving all daily tasks for user '{user_id}': {e}", exc_info=True)
    return all_tasks

# --- Funções de Access to Data Projects ---

async def get_project_data(user_id: str, project_id: str) -> dict:
    doc_ref = get_project_doc_ref(user_id, project_id)
    logger.debug(f"EIXA_DATA | Getting project '{project_id}' for user '{user_id}'. Doc path: {doc_ref.path}")
    doc = await asyncio.to_thread(doc_ref.get)
    if not doc.exists:
        logger.info(f"EIXA_DATA | Project '{project_id}' not found for user '{user_id}'. Returning empty dict.")
        return {}
    data = doc.to_dict()
    logger.debug(f"EIXA_DATA | Raw data fetched for project '{project_id}' for '{user_id}': {data}")
    return data

async def save_project_data(user_id: str, project_id: str, data: dict):
    doc_ref = get_project_doc_ref(user_id, project_id)
    logger.debug(f"EIXA_DATA | Attempting to save project '{project_id}' for user '{user_id}'. Doc path: {doc_ref.path}. Data: {data}")
    try:
        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | Project '{project_id}' for user '{user_id}' saved to Firestore successfully.")
    except Exception as e:
        # AQUI É A MUDANÇA: Log Critical Error e raise para que o erro seja propagado
        logger.critical(f"EIXA_DATA | CRITICAL ERROR: Failed to save project '{project_id}' to Firestore for user '{user_id}'. Doc Path: {doc_ref.path}. Error: {e}", exc_info=True)
        raise # Re-raise a exceção para que o orchestrator saiba que falhou

async def get_all_projects(user_id: str) -> list[dict]:
    projects_ref = get_user_subcollection(user_id, 'projects')
    # CORREÇÃO: Usar .id para CollectionReference (não tem .path)
    logger.debug(f"EIXA_DATA | Attempting to retrieve all projects for user '{user_id}' from collection ID: {projects_ref.id}. Full path: {projects_ref.parent.id}/{projects_ref.id}") # Adicionado full path para debug
    all_projects = []
    try:
        docs = await asyncio.to_thread(lambda: list(projects_ref.stream()))
        if not docs:
            logger.info(f"EIXA_DATA | No project documents found for user '{user_id}'.")
        for doc in docs:
            project_data = doc.to_dict()
            project_data["id"] = doc.id

            project_data.setdefault("name", project_data.get("nome", "Projeto sem nome"))
            project_data.setdefault("description", project_data.get("descricao", ""))
            project_data.setdefault("progress_tags", project_data.get("tags_progresso", ["open"]))

            if "micro_tasks" in project_data and isinstance(project_data["micro_tasks"], list):
                modern_microtasks = []
                for mt in project_data["micro_tasks"]:
                    if isinstance(mt, str):
                        modern_microtasks.append({"description": mt, "completed": False})
                        logger.warning(f"EIXA_DATA | Converted old string micro_task format for project '{doc.id}' of user '{user_id}': '{mt}'.")
                    elif isinstance(mt, dict):
                        mt.setdefault("description", "Microtarefa sem descrição")
                        mt.setdefault("completed", False)
                        modern_microtasks.append(mt)
                    else:
                        logger.warning(f"EIXA_DATA | Unexpected micro_task format for project '{doc.id}' of user '{user_id}': {mt}. Skipping.", exc_info=True)
                project_data["micro_tasks"] = modern_microtasks
            else:
                project_data.setdefault("micro_tasks", [])
            
            all_projects.append(project_data)
            logger.debug(f"EIXA_DATA | Retrieved project '{doc.id}' for user '{user_id}'.")

        logger.info(f"EIXA_DATA | Retrieved all projects for user '{user_id}'. Total projects: {len(all_projects)}")
    except Exception as e:
        logger.error(f"EIXA_DATA | Error retrieving all projects for user '{user_id}': {e}", exc_info=True)
    return all_projects

# --- Função para obter o histórico de interação do usuário ---
async def get_user_history(user_id: str, interactions_collection_logical_name: str = EIXA_INTERACTIONS_COLLECTION, limit: int = 10) -> list[dict]:
    try:
        interactions_ref = get_top_level_collection(interactions_collection_logical_name)
        db = _initialize_firestore_client_instance()
        
        # JÁ ESTAVA CORRIGIDO PARA .where(), NENHUMA MUDANÇA AQUI
        query = db.collection(interactions_ref.id).where('user_id', '==', user_id).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)

        docs = await asyncio.to_thread(lambda: list(query.stream()))
        history = []
        for doc in docs:
            history.append(doc.to_dict())

        history.reverse() # Mais recente no final
        logger.info(f"EIXA_DATA | Retrieved {len(history)} interaction history items for user '{user_id}'.")
        return history
    except Exception as e:
        logger.error(f"EIXA_DATA | Error retrieving user history for '{user_id}' from collection '{interactions_collection_logical_name}': {e}", exc_info=True)
        return []