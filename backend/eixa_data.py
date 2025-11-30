import logging
import uuid
from google.cloud import firestore
import asyncio
from datetime import datetime, timedelta, timezone, time
from typing import Dict, Any, List

from firestore_client_singleton import _initialize_firestore_client_instance
from config import (
    USERS_COLLECTION, EIXA_INTERACTIONS_COLLECTION,
    EIXA_ROUTINES_COLLECTION, EIXA_GOOGLE_AUTH_COLLECTION,
    SUBCOLLECTIONS_MAP
)
from collections_manager import (
    get_user_subcollection,
    get_task_doc_ref,
    get_project_doc_ref,
    get_top_level_collection,
    get_unscheduled_tasks_collection,
    get_unscheduled_task_doc_ref,
)
from google_calendar_utils import GoogleCalendarUtils

logger = logging.getLogger(__name__)

# Instância do GoogleCalendarUtils
google_calendar_utils = GoogleCalendarUtils()

# --- Funções Auxiliares Comuns ---
def _parse_time_str(time_str: str) -> time | None:
    """Tenta parsear uma string 'HH:MM' em um objeto datetime.time."""
    if not isinstance(time_str, str) or len(time_str) != 5 or time_str[2] != ':':
        return None
    try:
        hour, minute = map(int, time_str.split(':'))
        return datetime.min.replace(hour=hour, minute=minute).time()
    except ValueError:
        return None

def _sort_tasks_by_time(tasks: list) -> list:
    """Ordena uma lista de tarefas (dicts) pelo campo 'time'.
    Tarefas sem 'time' ou com 'time' inválido são colocadas no início,
    e depois as tarefas com tempo definido são ordenadas cronologicamente.
    """
    def sort_key(task):
        time_str = task.get("time")
        parsed_time = _parse_time_str(time_str)
        if parsed_time:
            return (1, parsed_time)
        return (0, 0)

    return sorted(tasks, key=sort_key)


# --- Tarefas não agendadas (Inbox) ---

async def get_all_unscheduled_tasks(user_id: str) -> List[dict]:
    """Retorna todas as tarefas sem data agendada."""
    collection_ref = get_unscheduled_tasks_collection(user_id)
    try:
        docs = await asyncio.to_thread(lambda: list(collection_ref.stream()))
        tasks = []
        for doc in docs:
            task_data = doc.to_dict() or {}
            task_data.setdefault("id", doc.id)
            task_data.setdefault("description", "Tarefa sem descrição")
            task_data.setdefault("status", "todo")
            task_data.setdefault("completed", task_data.get("status") == "done")
            task_data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            task_data.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
            tasks.append(task_data)
        return tasks
    except Exception as e:
        logger.error(f"EIXA_DATA | get_all_unscheduled_tasks: Failed to retrieve unscheduled tasks for '{user_id}': {e}", exc_info=True)
        return []

async def save_unscheduled_task(user_id: str, task_id: str, data: dict):
    doc_ref = get_unscheduled_task_doc_ref(user_id, task_id)
    logger.debug(f"EIXA_DATA | save_unscheduled_task: Saving unscheduled task '{task_id}' for user '{user_id}'. Path: {doc_ref.path}. Data: {data}")
    try:
        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | save_unscheduled_task: Unscheduled task '{task_id}' saved for user '{user_id}'.")
    except Exception as e:
        logger.critical(f"EIXA_DATA | save_unscheduled_task: Failed to persist unscheduled task '{task_id}' for user '{user_id}': {e}", exc_info=True)
        raise

async def delete_unscheduled_task(user_id: str, task_id: str):
    doc_ref = get_unscheduled_task_doc_ref(user_id, task_id)
    logger.debug(f"EIXA_DATA | delete_unscheduled_task: Removing unscheduled task '{task_id}' for user '{user_id}'. Path: {doc_ref.path}")
    try:
        await asyncio.to_thread(doc_ref.delete)
        logger.info(f"EIXA_DATA | delete_unscheduled_task: Unscheduled task '{task_id}' deleted for user '{user_id}'.")
    except Exception as e:
        logger.error(f"EIXA_DATA | delete_unscheduled_task: Failed to delete unscheduled task '{task_id}' for user '{user_id}': {e}", exc_info=True)
        raise

async def get_unscheduled_task(user_id: str, task_id: str) -> dict | None:
    doc_ref = get_unscheduled_task_doc_ref(user_id, task_id)
    doc = await asyncio.to_thread(doc_ref.get)
    if doc.exists:
        data = doc.to_dict() or {}
        data.setdefault("id", doc.id)
        return data
    return None


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
                modern_tasks.append({
                    "id": str(uuid.uuid4()),
                    "description": t,
                    "completed": False,
                    "time": "00:00",
                    "duration_minutes": 0,
                    "origin": "user_added",
                    "routine_item_id": None,
                    "google_calendar_event_id": None,
                    "is_synced_with_google_calendar": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                logger.warning(f"EIXA_DATA | Converted old string task format for '{user_id}' on '{date_str}': '{t}'.")
            elif isinstance(t, dict):
                t.setdefault("id", str(uuid.uuid4()))
                t.setdefault("description", "Tarefa sem descrição")
                t.setdefault("completed", False)
                t.setdefault("time", t.get("time", "00:00"))
                t.setdefault("duration_minutes", t.get("duration_minutes", 0))
                t.setdefault("origin", t.get("origin", "user_added"))
                t.setdefault("routine_item_id", t.get("routine_item_id", None))
                t.setdefault("google_calendar_event_id", t.get("google_calendar_event_id", None))
                t.setdefault("is_synced_with_google_calendar", False)
                t.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                t.setdefault("updated_at", t.get("updated_at", datetime.now(timezone.utc).isoformat()))
                modern_tasks.append(t)
            else:
                logger.critical(f"EIXA_DATA | UNEXPECTED TASK FORMAT. Task '{t}' for user '{user_id}' on '{date_str}' is neither string nor dict. Skipping.", exc_info=True)
        
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
        if "tasks" in data and isinstance(data["tasks"], list):
            data["tasks"] = _sort_tasks_by_time(data["tasks"])
        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | save_daily_tasks_data: Daily tasks for user '{user_id}' on '{date_str}' saved to Firestore successfully.")
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR: Failed to save daily tasks to Firestore for user '{user_id}' on '{date_str}'. Doc Path: {doc_ref.path}. Payload: {data}. Error: {e}", exc_info=True)
        raise

async def get_all_daily_tasks(user_id: str) -> dict:
    agenda_ref = get_user_subcollection(user_id, 'agenda')
    logger.debug(f"EIXA_DATA | get_all_daily_tasks: Attempting to retrieve all daily tasks for user '{user_id}' from collection ID: {agenda_ref.id}. Full path: {agenda_ref.parent.id}/{agenda_ref.id}")
    all_tasks = {}
    try:
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

# --- Gerenciamento de Rotinas (Templates) ---

async def get_routine_doc_ref(user_id: str, routine_id: str):
    db = _initialize_firestore_client_instance()
    return db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION).document(routine_id)

async def get_routine_template(user_id: str, routine_id_or_name: str) -> dict | None:
    db = _initialize_firestore_client_instance()
    routines_ref = db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION)
    
    doc_ref = routines_ref.document(routine_id_or_name)
    doc = await asyncio.to_thread(doc_ref.get)
    if doc.exists:
        logger.info(f"EIXA_DATA | get_routine_template: Routine '{routine_id_or_name}' found by ID for user '{user_id}'.")
        routine_data = doc.to_dict()
        routine_data['id'] = doc.id
        return routine_data
    
    query = routines_ref.where('name', '==', routine_id_or_name)
    docs = await asyncio.to_thread(lambda: list(query.stream()))
    if docs:
        if len(docs) > 1:
            logger.warning(f"EIXA_DATA | get_routine_template: Multiple routines found with name '{routine_id_or_name}' for user '{user_id}'. Returning the first one.")
        doc = docs[0]
        logger.info(f"EIXA_DATA | get_routine_template: Routine '{routine_id_or_name}' found by name for user '{user_id}'. ID: {doc.id}")
        routine_data = doc.to_dict()
        routine_data['id'] = doc.id
        return routine_data

    logger.warning(f"EIXA_DATA | get_routine_template: Routine '{routine_id_or_name}' not found by ID or name for user '{user_id}'.")
    return None

async def save_routine_template(user_id: str, routine_id: str, data: dict):
    db = _initialize_firestore_client_instance()
    doc_ref = db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION).document(routine_id)
    logger.debug(f"EIXA_DATA | save_routine_template: Saving routine '{routine_id}' for user '{user_id}'. Path: {doc_ref.parent.path}/{doc_ref.id}. Data: {data}") 
    try:
        current_time = datetime.now(timezone.utc).isoformat()
        data.setdefault("created_at", current_time)
        data["updated_at"] = current_time

        # NOVO: Adiciona o campo recurrence_rule se presente nos dados
        data.setdefault("recurrence_rule", None) 
        if 'recurrence_rule' in data and data['recurrence_rule'] is not None:
            # Garante que recurrence_rule seja string, caso venha de LLM como outro tipo
            data['recurrence_rule'] = str(data['recurrence_rule'])


        if 'schedule' in data and isinstance(data['schedule'], list):
            for item in data['schedule']:
                item.setdefault("created_at", current_time)
                item["updated_at"] = current_time
                item.setdefault("id", str(uuid.uuid4()))

        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | save_routine_template: Routine '{routine_id}' for user '{user_id}' saved successfully.")
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR: Failed to save routine '{routine_id}' for user '{user_id}'. Error: {e}", exc_info=True)
        raise

async def delete_routine_template(user_id: str, routine_id_or_name: str) -> Dict[str, Any]:
    db = _initialize_firestore_client_instance()
    routines_ref = db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION)
    
    routine_to_delete = await get_routine_template(user_id, routine_id_or_name)
    
    if routine_to_delete:
        doc_ref = routines_ref.document(routine_to_delete['id'])
        logger.debug(f"EIXA_DATA | delete_routine_template: Deleting routine '{routine_to_delete['id']}' for user '{user_id}'. Path: {doc_ref.path}")
        try:
            await asyncio.to_thread(doc_ref.delete)
            logger.info(f"EIXA_DATA | delete_routine_template: Routine '{routine_to_delete['id']}' for user '{user_id}' deleted successfully.")
            return {"status": "success", "message": f"Rotina '{routine_to_delete.get('name', routine_to_delete['id'])}' excluída com sucesso."}
        except Exception as e:
            logger.error(f"EIXA_DATA | Error deleting routine '{routine_to_delete['id']}' for user '{user_id}'. Error: {e}", exc_info=True)
            return {"status": "error", "message": "Falha ao excluir a rotina."}
    else:
        logger.warning(f"EIXA_DATA | delete_routine_template: Routine '{routine_id_or_name}' not found for user '{user_id}'.")
        return {"status": "not_found", "message": "Rotina não encontrada para exclusão."}

async def get_all_routines(user_id: str) -> list[dict]:
    db = _initialize_firestore_client_instance()
    routines_ref = db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION)
    logger.debug(f"EIXA_DATA | get_all_routines: Retrieving all routines for user '{user_id}'. Path: {routines_ref.parent.path}/{routines_ref.id}")
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

# --- Aplicar Rotina ao Dia ---

async def apply_routine_to_day(user_id: str, date_str: str, routine_id_or_name: str, conflict_strategy: str = "overwrite") -> Dict[str, Any]:
    """
    Aplica um cronograma de rotina a um dia específico do usuário.
    routine_id_or_name: O ID ou nome da rotina a ser aplicada.
    conflict_strategy: "overwrite" (apaga tarefas existentes) ou "merge" (mescla/adiciona).
    Retorna status e mensagem.
    """
    logger.info(f"EIXA_DATA | apply_routine_to_day: Applying routine to {date_str} for user {user_id} with strategy '{conflict_strategy}'.")
    
    routine_template = await get_routine_template(user_id, routine_id_or_name)
    if not routine_template:
        logger.warning(f"EIXA_DATA | apply_routine_to_day: Routine '{routine_id_or_name}' not found for user '{user_id}'. Cannot apply.")
        return {"status": "error", "message": f"Rotina '{routine_id_or_name}' não encontrada."}

    routine_schedule = routine_template.get('schedule', [])
    routine_name = routine_template.get('name', routine_id_or_name)

    if not routine_schedule:
        logger.info(f"EIXA_DATA | apply_routine_to_day: Routine '{routine_name}' has no tasks in its schedule. Nothing to apply.")
        return {"status": "info", "message": f"Rotina '{routine_name}' não possui tarefas. Nada foi aplicado."}

    current_daily_data = await get_daily_tasks_data(user_id, date_str)
    existing_tasks = current_daily_data.get("tasks", [])
    
    new_tasks_for_day = []

    if conflict_strategy == "overwrite":
        logger.info(f"EIXA_DATA | apply_routine_to_day: Overwriting existing tasks for {date_str}.")
    elif conflict_strategy == "merge":
        logger.info(f"EIXA_DATA | apply_routine_to_day: Merging with existing tasks for {date_str}.")
        new_tasks_for_day.extend(existing_tasks)
    else:
        logger.warning(f"EIXA_DATA | apply_routine_to_day: Unknown conflict strategy '{conflict_strategy}'. Defaulting to 'overwrite'.")
        
    for routine_item in routine_schedule:
        task = {
            "id": routine_item.get("id", str(uuid.uuid4())),
            "description": routine_item.get("description", "Tarefa da Rotina"),
            "completed": False,
            "time": routine_item.get("time", "00:00"),
            "duration_minutes": routine_item.get("duration_minutes", 0),
            "origin": "routine",
            "routine_item_id": routine_item.get("id"),
            "google_calendar_event_id": None,
            "is_synced_with_google_calendar": False,
            "created_at": routine_item.get("created_at", datetime.now(timezone.utc).isoformat()),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        if conflict_strategy == "merge":
            found_existing = False
            for i, existing_task in enumerate(new_tasks_for_day):
                if existing_task.get("routine_item_id") == task["routine_item_id"]:
                    new_tasks_for_day[i] = task
                    found_existing = True
                    break
                if existing_task.get("description", "").lower() == task["description"].lower() and \
                   existing_task.get("time") == task["time"] and \
                   existing_task.get("origin") != "google_calendar" and \
                   existing_task.get("completed") == False:
                    logger.warning(f"EIXA_DATA | apply_routine_to_day: Potential duplicate or time conflict for task '{task['description']}' at '{task['time']}'. Skipping addition for merge strategy.")
                    found_existing = True
                    break
            
            if not found_existing:
                new_tasks_for_day.append(task)
        elif conflict_strategy == "overwrite":
            new_tasks_for_day.append(task)

        logger.debug(f"EIXA_DATA | apply_routine_to_day: Processed task from routine: {task['description']} at {task['time']}")
    
    try:
        await save_daily_tasks_data(user_id, date_str, {"tasks": new_tasks_for_day})
        logger.info(f"EIXA_DATA | apply_routine_to_day: Routine '{routine_name}' applied to {date_str} for user {user_id}.")
        return {"status": "success", "message": f"Rotina '{routine_name}' aplicada com sucesso para {date_str}."}
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR applying routine '{routine_name}' to {date_str} for user {user_id}. Error: {e}", exc_info=True)
        return {"status": "error", "message": "Falha ao aplicar a rotina."}


# --- Integração com Google Calendar (Pull Events) ---

async def sync_google_calendar_events_to_eixa(user_id: str, start_date_obj: datetime, end_date_obj: datetime) -> Dict[str, Any]:
    """
    Puxa eventos do Google Calendar e os sincroniza com a agenda da EIXA.
    Retorna um dicionário com status e mensagem, e o número de eventos adicionados/atualizados.
    """
    logger.info(f"EIXA_DATA | sync_google_calendar_events_to_eixa: Syncing Google Calendar events for user {user_id} from {start_date_obj} to {end_date_obj}.")
    
    try:
        creds = await google_calendar_utils.get_credentials(user_id)
        if not creds:
            logger.warning(f"EIXA_DATA | sync_google_calendar_events_to_eixa: No Google Calendar credentials found for user {user_id}. Cannot sync.")
            return {"status": "error", "message": "Credenciais do Google Calendar não encontradas. Por favor, conecte sua conta."}

        google_events = await google_calendar_utils.list_calendar_events(user_id, start_date_obj, end_date_obj, credentials=creds)
        
        if not google_events:
            logger.info(f"EIXA_DATA | No Google Calendar events found for user {user_id} in the specified period.")
            return {"status": "info", "message": "Nenhum evento do Google Calendar encontrado para o período especificado."}

        added_count = 0
        updated_count = 0

        tasks_to_save_by_date = {}

        for gc_event in google_events:
            event_id = gc_event.get('id')
            summary = gc_event.get('summary', 'Evento sem título')
            
            start_info = gc_event.get('start')
            end_info = gc_event.get('end')

            if not start_info:
                logger.warning(f"EIXA_DATA | Google Calendar event {event_id} ({summary}) has no start time/date. Skipping.")
                continue

            event_start_dt = None
            event_end_dt = None
            is_all_day = False

            try:
                if start_info.get('dateTime'):
                    event_start_dt = datetime.fromisoformat(start_info['dateTime'])
                    if event_start_dt.tzinfo is None:
                        event_start_dt = event_start_dt.replace(tzinfo=timezone.utc)
                elif start_info.get('date'):
                    is_all_day = True
                    event_start_dt = datetime.fromisoformat(start_info['date']).replace(tzinfo=timezone.utc)
                
                if end_info and end_info.get('dateTime'):
                    event_end_dt = datetime.fromisoformat(end_info['dateTime'])
                    if event_end_dt.tzinfo is None:
                        event_end_dt = event_end_dt.replace(tzinfo=timezone.utc)
                elif end_info and end_info.get('date') and is_all_day:
                    event_end_dt = datetime.fromisoformat(end_info['date']).replace(tzinfo=timezone.utc)
                
                if not event_end_dt and event_start_dt:
                    event_end_dt = event_start_dt + timedelta(hours=1)
                
                if not event_start_dt or not event_end_dt: raise ValueError("Could not determine start or end datetime.")
                
                date_str = event_start_dt.strftime('%Y-%m-%d')
                time_str = event_start_dt.strftime('%H:%M')
                
                duration_minutes = 0
                if not is_all_day:
                    duration_minutes = int((event_end_dt - event_start_dt).total_seconds() / 60)
                else:
                    duration_days = (event_end_dt - event_start_dt).days
                    duration_minutes = duration_days * 24 * 60

                if duration_minutes < 0:
                    logger.warning(f"EIXA_DATA | Google Calendar event {event_id} ({summary}) has negative duration. Setting to 0.")
                    duration_minutes = 0

                if date_str not in tasks_to_save_by_date:
                    tasks_to_save_by_date[date_str] = await get_daily_tasks_data(user_id, date_str)
                current_daily_tasks = tasks_to_save_by_date[date_str].get("tasks", [])

                existing_eixa_task_index = next(
                    (i for i, t in enumerate(current_daily_tasks) if t.get('google_calendar_event_id') == event_id),
                    None
                )

                eixa_task = {
                    "id": current_daily_tasks[existing_eixa_task_index].get('id') if existing_eixa_task_index is not None else str(uuid.uuid4()),
                    "description": summary,
                    "completed": False,
                    "time": time_str,
                    "duration_minutes": duration_minutes,
                    "origin": "google_calendar",
                    "google_calendar_event_id": event_id,
                    "is_synced_with_google_calendar": True,
                    "created_at": current_daily_tasks[existing_eixa_task_index].get('created_at', datetime.now(timezone.utc).isoformat()) if existing_eixa_task_index is not None else datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

                if existing_eixa_task_index is not None:
                    logger.debug(f"EIXA_DATA | Updating existing EIXA task for GC event {event_id}: {summary}")
                    current_daily_tasks[existing_eixa_task_index] = eixa_task
                    updated_count += 1
                else:
                    logger.debug(f"EIXA_DATA | Adding new EIXA task from GC event {event_id}: {summary}")
                    current_daily_tasks.append(eixa_task)
                    added_count += 1
                
                tasks_to_save_by_date[date_str]["tasks"] = current_daily_tasks

            except ValueError as e:
                logger.error(f"EIXA_DATA | Could not parse date/time for Google Calendar event {event_id} ({summary}): {e}", exc_info=True)
            except Exception as e:
                logger.critical(f"EIXA_DATA | Unexpected error processing Google Calendar event {event_id} ({summary}): {e}", exc_info=True)

        for date_str, daily_data in tasks_to_save_by_date.items():
            await save_daily_tasks_data(user_id, date_str, daily_data)

        logger.info(f"EIXA_DATA | Finished syncing Google Calendar events for user {user_id}. Added: {added_count}, Updated: {updated_count}.")
        return {"status": "success", "message": f"Sincronização com Google Calendar concluída! {added_count} novos eventos e {updated_count} atualizados."}
    
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR during Google Calendar sync for user {user_id}: {e}", exc_info=True)
        return {"status": "error", "message": "Falha crítica ao sincronizar com o Google Calendar."}


# --- Funções de Access to Data Projects ---

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
            project_data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            project_data.setdefault("updated_at", project_data.get("updated_at", datetime.now(timezone.utc).isoformat()))

            if "micro_tasks" in project_data and isinstance(project_data["micro_tasks"], list):
                modern_microtasks = []
                for mt in project_data["micro_tasks"]:
                    if isinstance(mt, str):
                        modern_microtasks.append({"description": mt, "completed": False, "created_at": datetime.now(timezone.utc).isoformat(), "id": str(uuid.uuid4())})
                        logger.warning(f"EIXA_DATA | Converted old string micro_task format for project '{doc.id}' of user '{user_id}': '{mt}'.")
                    elif isinstance(mt, dict):
                        mt.setdefault("description", "Microtarefa sem descrição")
                        mt.setdefault("completed", False)
                        mt.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                        mt.setdefault("id", str(uuid.uuid4()))
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

# --- Função para obter o histórico de interação do usuário ---
async def get_user_history(user_id: str, interactions_collection_logical_name: str = EIXA_INTERACTIONS_COLLECTION, limit: int = 10) -> list[dict]:
    try:
        collection_real_name = get_top_level_collection(interactions_collection_logical_name).id
        db = _initialize_firestore_client_instance()
        
        logger.debug(f"EIXA_DATA | get_user_history: Querying history for user '{user_id}' from real collection '{collection_real_name}'. Limit: {limit}")
        query = db.collection(collection_real_name).where('user_id', '==', user_id).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)

        docs = await asyncio.to_thread(lambda: list(query.stream()))
        history = []
        for doc in docs:
            history.append(doc.to_dict())

        history.reverse()
        logger.info(f"EIXA_DATA | get_user_history: Retrieved {len(history)} interaction history items for user '{user_id}'.")
        return history
    except Exception as e:
        logger.error(f"EIXA_DATA | get_user_history: Error retrieving user history for '{user_id}' from collection '{interactions_collection_logical_name}': {e}", exc_info=True)
        return []