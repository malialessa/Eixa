import logging
import uuid
from google.cloud import firestore
import asyncio
from datetime import datetime, timedelta

from firestore_client_singleton import _initialize_firestore_client_instance
from config import (
    USERS_COLLECTION, EIXA_INTERACTIONS_COLLECTION, 
    EIXA_ROUTINES_COLLECTION, EIXA_GOOGLE_AUTH_COLLECTION, # NOVOS IMPORTS
    SUBCOLLECTIONS_MAP # Necessário para 'agenda' e 'projects'
)
from collections_manager import get_user_subcollection, get_task_doc_ref, get_project_doc_ref, get_top_level_collection
from google_calendar_utils import GoogleCalendarUtils, GOOGLE_CALENDAR_SCOPES # NOVO IMPORT

logger = logging.getLogger(__name__)

# Instância do GoogleCalendarUtils
google_calendar_utils = GoogleCalendarUtils()

# --- Funções Auxiliares Comuns ---
def _parse_time_str(time_str: str) -> datetime.time | None:
    """Tenta parsear uma string 'HH:MM' em um objeto datetime.time."""
    if not isinstance(time_str, str) or len(time_str) != 5 or time_str[2] != ':':
        return None
    try:
        hour, minute = map(int, time_str.split(':'))
        return datetime.min.replace(hour=hour, minute=minute).time()
    except ValueError:
        return None

def _sort_tasks_by_time(tasks: list) -> list:
    """Ordena uma lista de tarefas (dicts) pelo campo 'time'."""
    # Define uma função de chave de ordenação que prioriza tarefas com tempo definido
    # Tarefas sem tempo ou com tempo inválido vão para o final
    def sort_key(task):
        time_str = task.get("time")
        parsed_time = _parse_time_str(time_str)
        if parsed_time:
            return parsed_time
        return datetime.max.time() # Coloca tarefas sem tempo no final

    return sorted(tasks, key=sort_key)


# --- Funções de Access to Data Agenda (Daily Tasks) ---

async def get_daily_tasks_data(user_id: str, date_str: str) -> dict:
    doc_ref = get_task_doc_ref(user_id, date_str)
    logger.debug(f"EIXA_DATA | get_daily_tasks_data: Getting daily tasks for user '{user_id}' on date '{date_str}'. Doc path: {doc_ref.path}")
    doc = await asyncio.to_thread(doc_ref.get)

    if not doc.exists:
        logger.info(f"EIXA_DATA | get_daily_tasks_data: No daily tasks document found for user '{user_id}' on '{date_str}'. Returning empty list.")
        return {"tasks": []}

    data = doc.to_dict()
    logger.debug(f"EIXA_DATA | get_daily_tasks_data: Raw data fetched for daily tasks for '{user_id}' on '{date_str}': {data}")

    if "tasks" in data and isinstance(data["tasks"], list):
        modern_tasks = []
        for t in data["tasks"]:
            if isinstance(t, str):
                # Conversão de formato antigo para o novo com defaults
                modern_tasks.append({
                    "id": str(uuid.uuid4()),
                    "description": t,
                    "completed": False,
                    "time": "00:00", # DEFAULT TIME
                    "duration_minutes": 0, # DEFAULT DURATION
                    "origin": "user_added",
                    "routine_item_id": None,
                    "google_calendar_event_id": None,
                    "is_synced_with_google_calendar": False
                })
                logger.warning(f"EIXA_DATA | Converted old string task format for '{user_id}' on '{date_str}': '{t}'.")
            elif isinstance(t, dict):
                # Garante que todos os novos campos estão presentes com defaults
                t.setdefault("id", str(uuid.uuid4()))
                t.setdefault("description", "Tarefa sem descrição")
                t.setdefault("completed", False)
                t.setdefault("time", t.get("time", "00:00")) # Garante 'HH:MM'
                t.setdefault("duration_minutes", t.get("duration_minutes", 0)) # Garante inteiro
                t.setdefault("origin", t.get("origin", "user_added"))
                t.setdefault("routine_item_id", t.get("routine_item_id", None))
                t.setdefault("google_calendar_event_id", t.get("google_calendar_event_id", None))
                t.setdefault("is_synced_with_google_calendar", t.get("is_synced_with_google_calendar", False))
                modern_tasks.append(t)
            else:
                logger.critical(f"EIXA_DATA | UNEXPECTED TASK FORMAT. Task '{t}' for user '{user_id}' on '{date_str}' is neither string nor dict. Skipping.", exc_info=True)
        
        # Ordena as tarefas pelo tempo
        data["tasks"] = _sort_tasks_by_time(modern_tasks)
    else:
        logger.critical(f"EIXA_DATA | CRITICAL: Document for '{user_id}' on '{date_str}' does NOT contain a 'tasks' list or 'tasks' field is missing. Data: {data}. Initializing 'tasks' as empty.", exc_info=True)
        data["tasks"] = []
    
    logger.debug(f"EIXA_DATA | get_daily_tasks_data: Processed daily tasks data for '{user_id}' on '{date_str}': {data}")
    return data

async def save_daily_tasks_data(user_id: str, date_str: str, data: dict):
    doc_ref = get_task_doc_ref(user_id, date_str)
    logger.debug(f"EIXA_DATA | save_daily_tasks_data: Attempting to save daily tasks for user '{user_id}' on '{date_str}'. Doc path: {doc_ref.path}. Data: {data}")
    try:
        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | save_daily_tasks_data: Daily tasks for user '{user_id}' on '{date_str}' saved to Firestore successfully.")
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR: Failed to save daily tasks to Firestore for user '{user_id}' on '{date_str}'. Doc Path: {doc_ref.path}. Payload: {data}. Error: {e}", exc_info=True)
        raise # Re-raise a exceção para que o orchestrator saiba que falhou

async def get_all_daily_tasks(user_id: str) -> dict:
    agenda_ref = get_user_subcollection(user_id, 'agenda')
    logger.debug(f"EIXA_DATA | get_all_daily_tasks: Attempting to retrieve all daily tasks for user '{user_id}' from collection ID: {agenda_ref.id}. Full path: {agenda_ref.parent.id}/{agenda_ref.id}")
    all_tasks = {}
    try:
        # Nota: agenda_ref.stream() retorna documentos de cada dia. O get_daily_tasks_data já ordena as tarefas dentro do dia.
        docs = await asyncio.to_thread(lambda: list(agenda_ref.stream()))
        if not docs:
            logger.info(f"EIXA_DATA | get_all_daily_tasks: No daily task documents found for user '{user_id}'.")
        for doc_snapshot in docs:
            date_str = doc_snapshot.id
            all_tasks[date_str] = await get_daily_tasks_data(user_id, date_str)
            logger.debug(f"EIXA_DATA | get_all_daily_tasks: Retrieved doc '{date_str}' for user '{user_id}'.")
        logger.info(f"EIXA_DATA | get_all_daily_tasks: Retrieved all daily tasks for user '{user_id}'. Total days: {len(all_tasks)}")
    except Exception as e:
        logger.error(f"EIXA_DATA | get_all_daily_tasks: Error retrieving all daily tasks for user '{user_id}': {e}", exc_info=True)
    return all_tasks

# --- NOVAS FUNÇÕES: Gerenciamento de Rotinas (Templates) ---

async def get_routine_doc_ref(user_id: str, routine_id: str):
    db = _initialize_firestore_client_instance()
    return db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION).document(routine_id)

async def get_routine_template(user_id: str, routine_id: str) -> dict | None:
    doc_ref = await get_routine_doc_ref(user_id, routine_id)
    logger.debug(f"EIXA_DATA | get_routine_template: Getting routine '{routine_id}' for user '{user_id}'. Path: {doc_ref.path}")
    doc = await asyncio.to_thread(doc_ref.get)
    if doc.exists:
        logger.info(f"EIXA_DATA | get_routine_template: Routine '{routine_id}' found for user '{user_id}'.")
        return doc.to_dict()
    logger.warning(f"EIXA_DATA | get_routine_template: Routine '{routine_id}' not found for user '{user_id}'.")
    return None

async def save_routine_template(user_id: str, routine_id: str, data: dict):
    doc_ref = await get_routine_doc_ref(user_id, routine_id)
    logger.debug(f"EIXA_DATA | save_routine_template: Saving routine '{routine_id}' for user '{user_id}'. Path: {doc_ref.path}. Data: {data}")
    try:
        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | save_routine_template: Routine '{routine_id}' for user '{user_id}' saved successfully.")
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR: Failed to save routine '{routine_id}' for user '{user_id}'. Error: {e}", exc_info=True)
        raise

async def delete_routine_template(user_id: str, routine_id: str):
    doc_ref = await get_routine_doc_ref(user_id, routine_id)
    logger.debug(f"EIXA_DATA | delete_routine_template: Deleting routine '{routine_id}' for user '{user_id}'. Path: {doc_ref.path}")
    try:
        await asyncio.to_thread(doc_ref.delete)
        logger.info(f"EIXA_DATA | delete_routine_template: Routine '{routine_id}' for user '{user_id}' deleted successfully.")
    except Exception as e:
        logger.error(f"EIXA_DATA | Error deleting routine '{routine_id}' for user '{user_id}'. Error: {e}", exc_info=True)
        raise

async def get_all_routines(user_id: str) -> list[dict]:
    db = _initialize_firestore_client_instance()
    routines_ref = db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION)
    logger.debug(f"EIXA_DATA | get_all_routines: Retrieving all routines for user '{user_id}'. Path: {routines_ref.path}")
    all_routines = []
    try:
        docs = await asyncio.to_thread(lambda: list(routines_ref.stream()))
        for doc in docs:
            routine_data = doc.to_dict()
            routine_data['id'] = doc.id
            all_routines.append(routine_data)
        logger.info(f"EIXA_DATA | get_all_routines: Found {len(all_routines)} routines for user '{user_id}'.")
    except Exception as e:
        logger.error(f"EIXA_DATA | Error retrieving all routines for user '{user_id}'. Error: {e}", exc_info=True)
    return all_routines

# --- NOVA FUNÇÃO: Aplicar Rotina ao Dia ---

async def apply_routine_to_day(user_id: str, date_str: str, routine_schedule: list, conflict_strategy: str = "overwrite"):
    """
    Aplica um cronograma de rotina a um dia específico do usuário.
    conflict_strategy: "overwrite" (apaga tarefas existentes) ou "merge" (mescla/adiciona).
    """
    logger.info(f"EIXA_DATA | apply_routine_to_day: Applying routine to {date_str} for user {user_id} with strategy '{conflict_strategy}'.")
    
    current_daily_data = await get_daily_tasks_data(user_id, date_str)
    existing_tasks = current_daily_data.get("tasks", [])
    
    new_tasks_for_day = []

    if conflict_strategy == "overwrite":
        logger.info(f"EIXA_DATA | apply_routine_to_day: Overwriting existing tasks for {date_str}.")
        # Se sobrescrever, começamos com uma lista vazia e adicionamos apenas as da rotina
    elif conflict_strategy == "merge":
        logger.info(f"EIXA_DATA | apply_routine_to_day: Merging with existing tasks for {date_str}.")
        # Se mesclar, começamos com as tarefas existentes
        new_tasks_for_day.extend(existing_tasks)
    else:
        logger.warning(f"EIXA_DATA | apply_routine_to_day: Unknown conflict strategy '{conflict_strategy}'. Defaulting to 'overwrite'.")
        # Default to overwrite for safety if strategy is invalid
        pass # new_tasks_for_day will remain empty

    for routine_item in routine_schedule:
        # Garante que cada item da rotina tem os campos necessários para uma tarefa
        task = {
            "id": str(uuid.uuid4()), # Novo ID para a tarefa do dia
            "description": routine_item.get("description", "Tarefa da Rotina"),
            "completed": False,
            "time": routine_item.get("time", "00:00"),
            "duration_minutes": routine_item.get("duration_minutes", 0),
            "origin": "routine_template",
            "routine_item_id": routine_item.get("id"), # Referência ao ID do item no template da rotina
            "google_calendar_event_id": None,
            "is_synced_with_google_calendar": False
        }
        
        # Lógica de merge simples: Se já existe uma tarefa no MESMO horário, a nova sobrescreve.
        # Para uma lógica mais avançada (ex: mover), o LLM precisaria orquestrar isso.
        if conflict_strategy == "merge":
            # Remove qualquer tarefa existente no mesmo horário antes de adicionar a nova
            # (ou você pode querer uma pergunta ao usuário via LLM)
            new_tasks_for_day = [
                t for t in new_tasks_for_day if t.get("time") != task["time"]
            ]
        
        new_tasks_for_day.append(task)
        logger.debug(f"EIXA_DATA | apply_routine_to_day: Added task from routine: {task['description']} at {task['time']}")
    
    # Salva o estado final da agenda do dia, garantindo que esteja ordenada
    await save_daily_tasks_data(user_id, date_str, {"tasks": _sort_tasks_by_time(new_tasks_for_day)})
    logger.info(f"EIXA_DATA | apply_routine_to_day: Routine applied to {date_str} for user {user_id}.")


# --- NOVAS FUNÇÕES: Integração com Google Calendar (Pull Events) ---

async def sync_google_calendar_events_to_eixa(user_id: str, start_date: datetime, end_date: datetime) -> list:
    """
    Puxa eventos do Google Calendar e os sincroniza com a agenda da EIXA.
    Retorna uma lista de tarefas da EIXA que foram adicionadas/atualizadas.
    """
    logger.info(f"EIXA_DATA | sync_google_calendar_events_to_eixa: Syncing Google Calendar events for user {user_id} from {start_date} to {end_date}.")
    
    google_events = await google_calendar_utils.list_calendar_events(user_id, start_date, end_date)
    
    if not google_events:
        logger.info(f"EIXA_DATA | No Google Calendar events found for user {user_id} in the specified period.")
        return []

    updated_eixa_tasks = []
    db = _initialize_firestore_client_instance() # Obter instância do Firestore

    for gc_event in google_events:
        event_id = gc_event.get('id')
        summary = gc_event.get('summary', 'Evento sem título')
        start = gc_event.get('start', {}).get('dateTime') or gc_event.get('start', {}).get('date')
        end = gc_event.get('end', {}).get('dateTime') or gc_event.get('end', {}).get('date')

        if not start:
            logger.warning(f"EIXA_DATA | Google Calendar event {event_id} has no start time/date. Skipping: {summary}")
            continue

        try:
            # Tenta parsear como datetime, se falhar, tenta como date
            event_start_dt = datetime.fromisoformat(start)
            event_end_dt = datetime.fromisoformat(end) if end else event_start_dt + timedelta(hours=1) # Default 1 hora
            
            # Calcular duração em minutos
            duration_minutes = int((event_end_dt - event_start_dt).total_seconds() / 60)

            date_str = event_start_dt.strftime('%Y-%m-%d')
            time_str = event_start_dt.strftime('%H:%M')

            # Recupera as tarefas do dia atual para verificar se o evento já existe
            daily_data = await get_daily_tasks_data(user_id, date_str)
            existing_tasks = daily_data.get("tasks", [])

            # Verifica se o evento já existe na EIXA para evitar duplicação ou para atualizar
            existing_eixa_task = next(
                (t for t in existing_tasks if t.get('google_calendar_event_id') == event_id),
                None
            )

            eixa_task = {
                "id": existing_eixa_task.get('id') if existing_eixa_task else str(uuid.uuid4()),
                "description": summary,
                "completed": False, # Eventos do GC não são "completados" no sentido de uma tarefa
                "time": time_str,
                "duration_minutes": duration_minutes,
                "origin": "google_calendar",
                "google_calendar_event_id": event_id,
                "is_synced_with_google_calendar": True
            }

            if existing_eixa_task:
                # Atualiza a tarefa existente
                logger.debug(f"EIXA_DATA | Updating existing EIXA task for GC event {event_id}: {summary}")
                for i, task in enumerate(existing_tasks):
                    if task.get('id') == eixa_task['id']:
                        existing_tasks[i] = eixa_task # Substitui a tarefa existente
                        break
            else:
                # Adiciona nova tarefa
                logger.debug(f"EIXA_DATA | Adding new EIXA task from GC event {event_id}: {summary}")
                existing_tasks.append(eixa_task)
            
            # Salva o estado atualizado do dia
            await save_daily_tasks_data(user_id, date_str, {"tasks": existing_tasks})
            updated_eixa_tasks.append(eixa_task)

        except ValueError as e:
            logger.error(f"EIXA_DATA | Could not parse date/time for Google Calendar event {event_id} ({summary}): {e}", exc_info=True)
        except Exception as e:
            logger.critical(f"EIXA_DATA | Unexpected error processing Google Calendar event {event_id} ({summary}): {e}", exc_info=True)

    logger.info(f"EIXA_DATA | Finished syncing Google Calendar events for user {user_id}. {len(updated_eixa_tasks)} tasks added/updated.")
    return updated_eixa_tasks


# --- Funções de Access to Data Projects (Nenhuma alteração aqui, mas manter para referência) ---

async def get_project_data(user_id: str, project_id: str) -> dict:
    doc_ref = get_project_doc_ref(user_id, project_id)
    logger.debug(f"EIXA_DATA | get_project_data: Getting project '{project_id}' for user '{user_id}'. Doc path: {doc_ref.path}")
    doc = await asyncio.to_thread(doc_ref.get)
    if not doc.exists:
        logger.info(f"EIXA_DATA | get_project_data: Project '{project_id}' not found for user '{user_id}'. Returning empty dict.")
        return {}
    data = doc.to_dict()
    logger.debug(f"EIXA_DATA | get_project_data: Raw data fetched for project '{project_id}' for '{user_id}': {data}")
    return data

async def save_project_data(user_id: str, project_id: str, data: dict):
    doc_ref = get_project_doc_ref(user_id, project_id)
    logger.debug(f"EIXA_DATA | save_project_data: Attempting to save project '{project_id}' for user '{user_id}'. Doc path: {doc_ref.path}. Data: {data}")
    try:
        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | save_project_data: Project '{project_id}' for user '{user_id}' saved to Firestore successfully.")
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR: Failed to save project '{project_id}' to Firestore for user '{user_id}'. Doc Path: {doc_ref.path}. Error: {e}", exc_info=True)
        raise

async def get_all_projects(user_id: str) -> list[dict]:
    projects_ref = get_user_subcollection(user_id, 'projects')
    logger.debug(f"EIXA_DATA | get_all_projects: Attempting to retrieve all projects for user '{user_id}' from collection ID: {projects_ref.id}. Full path: {projects_ref.parent.path}/{projects_ref.id}")
    all_projects = []
    try:
        docs = await asyncio.to_thread(lambda: list(projects_ref.stream()))
        if not docs:
            logger.info(f"EIXA_DATA | get_all_projects: No project documents found for user '{user_id}'.")
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
            logger.debug(f"EIXA_DATA | get_all_projects: Retrieved project '{doc.id}' for user '{user_id}'.")

        logger.info(f"EIXA_DATA | get_all_projects: Retrieved all projects for user '{user_id}'. Total projects: {len(all_projects)}")
    except Exception as e:
        logger.error(f"EIXA_DATA | get_all_projects: Error retrieving all projects for user '{user_id}': {e}", exc_info=True)
    return all_projects

# --- Função para obter o histórico de interação do usuário (Manter, mas com adaptações se necessário) ---
async def get_user_history(user_id: str, interactions_collection_logical_name: str = EIXA_INTERACTIONS_COLLECTION, limit: int = 10) -> list[dict]:
    try:
        interactions_ref = get_top_level_collection(interactions_collection_logical_name)
        db = _initialize_firestore_client_instance()
        
        logger.debug(f"EIXA_DATA | get_user_history: Querying history for user '{user_id}' from collection '{interactions_ref.id}'. Limit: {limit}")
        query = db.collection(interactions_ref.id).where('user_id', '==', user_id).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)

        docs = await asyncio.to_thread(lambda: list(query.stream()))
        history = []
        for doc in docs:
            history.append(doc.to_dict())

        history.reverse() # Mais recente no final
        logger.info(f"EIXA_DATA | get_user_history: Retrieved {len(history)} interaction history items for user '{user_id}'.")
        return history
    except Exception as e:
        logger.error(f"EIXA_DATA | get_user_history: Error retrieving user history for '{user_id}' from collection '{interactions_collection_logical_name}': {e}", exc_info=True)
        return []