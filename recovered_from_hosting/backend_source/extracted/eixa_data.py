import logging
import unicodedata
import uuid
from collections import defaultdict
from google.cloud import firestore
import asyncio
from datetime import datetime, timedelta, timezone, time
from typing import Dict, Any, List, Optional

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


def _normalize_text(value: Any) -> str:
    """Normaliza strings removendo acentos e padronizando espaços."""
    if value is None:
        return ""
    text = str(value)
    normalized = unicodedata.normalize("NFD", text)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.upper()
    normalized = normalized.replace("-", " ").replace("_", " ")
    while "  " in normalized:
        normalized = normalized.replace("  ", " ")
    return normalized.strip()


def _normalize_day_token(value: Any) -> str:
    normalized = _normalize_text(value)
    normalized = normalized.replace("FEIRA", "").strip()
    return normalized


_DAY_NAME_ENTRIES: Dict[int, List[str]] = {
    0: ["MONDAY", "MON", "SEGUNDA", "SEG", "SEGUNDA-FEIRA"],
    1: ["TUESDAY", "TUE", "TERCA", "TERÇA", "TER", "TERCA-FEIRA"],
    2: ["WEDNESDAY", "WED", "QUARTA", "QUA", "QUARTA-FEIRA"],
    3: ["THURSDAY", "THU", "QUINTA", "QUI", "QUINTA-FEIRA"],
    4: ["FRIDAY", "FRI", "SEXTA", "SEX", "SEXTA-FEIRA"],
    5: ["SATURDAY", "SAT", "SABADO", "SAB", "SABADO-FEIRA"],
    6: ["SUNDAY", "SUN", "DOMINGO", "DOM"],
}

DAY_ALIAS_MAP: Dict[str, int] = {}
for idx, aliases in _DAY_NAME_ENTRIES.items():
    for alias in aliases:
        DAY_ALIAS_MAP[_normalize_day_token(alias)] = idx

DAILY_KEYWORDS = {
    _normalize_text(token)
    for token in [
        "todo dia",
        "todos os dias",
        "diario",
        "diária",
        "diarias",
        "daily",
        "every day",
        "everyday",
    ]
}

WEEKDAY_KEYWORDS = {
    _normalize_text(token)
    for token in ["dias uteis", "dias úteis", "weekday", "weekdays"]
}

WEEKEND_KEYWORDS = {
    _normalize_text(token)
    for token in ["fim de semana", "final de semana", "weekend", "fds"]
}


def _normalize_day_entry(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 6 else None
    text = _normalize_day_token(value)
    if not text:
        return None
    if text.isdigit():
        try:
            number = int(text)
            if 0 <= number <= 6:
                return number
        except ValueError:
            pass
    return DAY_ALIAS_MAP.get(text)


def _infer_weekdays_from_text(text: Any) -> set[int]:
    normalized = _normalize_text(text)
    if not normalized:
        return set()
    if any(keyword in normalized for keyword in DAILY_KEYWORDS):
        return set(range(7))
    if any(keyword in normalized for keyword in WEEKDAY_KEYWORDS):
        return {0, 1, 2, 3, 4}
    if any(keyword in normalized for keyword in WEEKEND_KEYWORDS):
        return {5, 6}
    inferred = set()
    for alias, idx in DAY_ALIAS_MAP.items():
        if alias and alias in normalized:
            inferred.add(idx)
    return inferred


def _extract_weekdays_from_routine(routine: Dict[str, Any]) -> set[int]:
    candidates = []
    for key in ("days_of_week", "days", "weekdays", "recurrence_days"):
        value = routine.get(key)
        if value:
            candidates.append(value)

    normalized_field = routine.get("normalized_weekdays")
    if normalized_field:
        candidates.append(normalized_field)

    normalized_days: set[int] = set()
    for candidate in candidates:
        if isinstance(candidate, (list, tuple, set)):
            iterable = candidate
        else:
            iterable = str(candidate).split(",")
        for entry in iterable:
            day_idx = _normalize_day_entry(entry)
            if day_idx is not None:
                normalized_days.add(day_idx)

    if normalized_days:
        return normalized_days

    recurrence_text = routine.get("recurrence_rule")
    description_text = routine.get("description")
    fallback_text = recurrence_text or description_text or routine.get("name", "")
    return _infer_weekdays_from_text(fallback_text)


def _routine_present_in_tasks(tasks: List[Dict[str, Any]], routine_id: str, schedule: List[Dict[str, Any]]) -> bool:
    if not tasks:
        return False
    if routine_id:
        for task in tasks:
            if task.get("routine_template_id") == routine_id:
                return True

    schedule_ids = {item.get("id") for item in schedule if item.get("id")}
    if not schedule_ids:
        return False
    existing_ids = {task.get("routine_item_id") for task in tasks if task.get("routine_item_id")}
    return bool(existing_ids) and schedule_ids.issubset(existing_ids)

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


MICRO_TASK_DATE_KEYS = ("due_date", "deadline", "target_date", "date", "scheduled_for", "day")


def _normalize_date_str(value: Any) -> Optional[str]:
    """Normaliza diferentes representações de data para o formato ISO YYYY-MM-DD."""
    if not value:
        return None

    if isinstance(value, str):
        try:
            if len(value) == 10:
                datetime.strptime(value, "%Y-%m-%d")
                return value
            parsed = datetime.fromisoformat(value)
            return parsed.date().isoformat()
        except ValueError:
            return None

    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, dict):
        seconds_value = value.get("seconds")
        if isinstance(seconds_value, (int, float)):
            try:
                return datetime.fromtimestamp(seconds_value, tz=timezone.utc).date().isoformat()
            except Exception:
                return None

    return None


def _normalize_time_str(value: Any) -> Optional[str]:
    """Retorna uma string HH:MM válida caso possível."""
    if isinstance(value, str):
        candidate = value[:5]
        if _parse_time_str(candidate):
            return candidate
    return None


def _safe_int(value: Any) -> Optional[int]:
    """Converte valores diversos em int quando possível."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


WEEKDAY_LABELS_PT = [
    "SEG", "TER", "QUA", "QUI", "SEX", "SAB", "DOM"
]


def _format_weekday_indices(day_indices: set[int]) -> str:
    if not day_indices:
        return "Dias não definidos"
    if len(day_indices) == 7:
        return "Todos os dias"
    if day_indices == {0, 1, 2, 3, 4}:
        return "Dias úteis"
    if day_indices == {5, 6}:
        return "Finais de semana"

    ordered = sorted(day_indices)
    labels = []
    for idx in ordered:
        try:
            labels.append(WEEKDAY_LABELS_PT[idx])
        except IndexError:
            continue
    return ", ".join(labels) if labels else "Dias não definidos"


def _sanitize_routine_schedule(schedule: Optional[List[Dict[str, Any]]], *, for_storage: bool = False) -> List[Dict[str, Any]]:
    if not schedule:
        return []

    sanitized_items: List[Dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for idx, raw_item in enumerate(schedule):
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        time_candidates = [
            item.get("time"),
            item.get("hour"),
            item.get("start_time"),
            item.get("scheduled_time"),
            item.get("schedule_time"),
        ]
        normalized_time: Optional[str] = None
        for candidate in time_candidates:
            normalized_time = _normalize_time_str(candidate)
            if normalized_time:
                break

        if not normalized_time:
            normalized_time = f"{(6 + idx) % 24:02d}:00"

        duration_value = _safe_int(item.get("duration_minutes"))

        item["time"] = normalized_time
        item["description"] = item.get("description") or item.get("name") or f"Item {idx + 1}"
        item["duration_minutes"] = duration_value if duration_value is not None else 0
        item["id"] = str(item.get("id") or uuid.uuid4())
        item["type"] = item.get("type") or "task"

        if for_storage:
            item["created_at"] = item.get("created_at") or now_iso
            item["updated_at"] = now_iso

        sanitized_items.append(item)

    return sanitized_items


def _attach_routine_metadata(routine: Dict[str, Any]) -> Dict[str, Any]:
    routine_copy = dict(routine)
    routine_copy["schedule"] = _sanitize_routine_schedule(routine_copy.get("schedule"), for_storage=False)
    normalized_days = _extract_weekdays_from_routine(routine_copy)
    routine_copy["normalized_weekdays"] = sorted(normalized_days)
    routine_copy["display_days"] = _format_weekday_indices(normalized_days)
    return routine_copy


def _normalize_microtask_for_agenda(project: Dict[str, Any], micro_task: Dict[str, Any], *, now_iso: str) -> Dict[str, Any]:
    """Cria um dicionário padronizado de microtarefa para uso na agenda."""
    due_date: Optional[str] = None
    for key in MICRO_TASK_DATE_KEYS:
        due_date = _normalize_date_str(micro_task.get(key))
        if due_date:
            break

    time_value: Optional[str] = None
    for key in ("due_time", "time", "start_time", "hour"):
        time_value = _normalize_time_str(micro_task.get(key))
        if time_value:
            break

    duration_minutes = _safe_int(
        micro_task.get("duration_minutes") or micro_task.get("estimated_duration_minutes")
    )

    normalized = {
        "id": micro_task.get("id", str(uuid.uuid4())),
        "description": micro_task.get("description", "Microtarefa sem descrição"),
        "completed": bool(micro_task.get("completed", False)),
        "status": micro_task.get("status") or ("completed" if micro_task.get("completed") else "todo"),
        "due_date": due_date,
        "time": time_value,
        "due_time": micro_task.get("due_time") or time_value,
        "duration_minutes": duration_minutes,
        "priority": micro_task.get("priority"),
        "impact_level": micro_task.get("impact_level"),
        "energy_level": micro_task.get("energy_level"),
        "notes": micro_task.get("notes"),
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "project_status": project.get("status"),
        "project_tags": project.get("progress_tags", []),
        "order_index": _safe_int(micro_task.get("order") or micro_task.get("index")),
        "origin": "micro_task",
        "created_at": micro_task.get("created_at") or now_iso,
        "updated_at": micro_task.get("updated_at") or now_iso
    }

    return normalized


def _microtask_sort_key(microtask: Dict[str, Any]) -> tuple:
    completed_flag = 1 if microtask.get("completed") else 0
    due_date = microtask.get("due_date") or "9999-12-31"
    order_key = microtask.get("order_index")
    order_key = order_key if isinstance(order_key, int) else 9999
    time_candidate = microtask.get("time") or microtask.get("due_time")
    parsed_time = _parse_time_str(time_candidate) if time_candidate else None
    time_key = parsed_time.strftime("%H:%M") if parsed_time else "99:99"
    description = microtask.get("description", "")
    created_at = microtask.get("created_at") or ""
    return (completed_flag, due_date, order_key, time_key, description, created_at)


def _sort_microtasks(microtasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ordena microtarefas priorizando pendentes e datas mais próximas."""
    if not microtasks:
        return []
    return sorted(microtasks, key=_microtask_sort_key)


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
        routine_data = doc.to_dict() or {}
        routine_data['id'] = doc.id
        return _attach_routine_metadata(routine_data)
    
    query = routines_ref.where('name', '==', routine_id_or_name)
    docs = await asyncio.to_thread(lambda: list(query.stream()))
    if docs:
        if len(docs) > 1:
            logger.warning(f"EIXA_DATA | get_routine_template: Multiple routines found with name '{routine_id_or_name}' for user '{user_id}'. Returning the first one.")
        doc = docs[0]
        logger.info(f"EIXA_DATA | get_routine_template: Routine '{routine_id_or_name}' found by name for user '{user_id}'. ID: {doc.id}")
        routine_data = doc.to_dict() or {}
        routine_data['id'] = doc.id
        return _attach_routine_metadata(routine_data)

    logger.warning(f"EIXA_DATA | get_routine_template: Routine '{routine_id_or_name}' not found by ID or name for user '{user_id}'.")
    return None

async def save_routine_template(user_id: str, routine_id: str, data: dict):
    db = _initialize_firestore_client_instance()
    doc_ref = db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION).document(routine_id)
    logger.debug(
        "EIXA_DATA | save_routine_template: Saving routine '%s' for user '%s'. Path: users/%s/%s/%s. Data: %s",
        routine_id,
        user_id,
        user_id,
        EIXA_ROUTINES_COLLECTION,
        routine_id,
        data,
    ) 
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
            data['schedule'] = _sanitize_routine_schedule(data['schedule'], for_storage=True)
        else:
            data['schedule'] = []

        normalized_days = _extract_weekdays_from_routine(data)
        data['normalized_weekdays'] = sorted(normalized_days)
        data['display_days'] = _format_weekday_indices(normalized_days)

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
    logger.debug(
        "EIXA_DATA | get_all_routines: Retrieving all routines for user '%s'. Path: users/%s/%s",
        user_id,
        user_id,
        EIXA_ROUTINES_COLLECTION,
    )
    all_routines = []
    try:
        docs = await asyncio.to_thread(lambda: list(routines_ref.stream()))
        for doc in docs:
            routine_data = doc.to_dict() or {}
            routine_data['id'] = doc.id
            all_routines.append(_attach_routine_metadata(routine_data))
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
            "routine_template_id": routine_template.get("id", routine_id_or_name),
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


async def ensure_recurring_routines(user_id: str, *, days_ahead: int = 14, routines: Optional[List[Dict[str, Any]]] = None) -> None:
    """Aplica automaticamente rotinas recorrentes para dias futuros definidos."""
    try:
        routines_list = routines or await get_all_routines(user_id)
    except Exception as exc:
        logger.error(f"EIXA_DATA | ensure_recurring_routines: Failed to fetch routines for user '{user_id}': {exc}", exc_info=True)
        return

    if not routines_list:
        return

    today = datetime.now(timezone.utc).date()
    cached_daily_data: Dict[str, Dict[str, Any]] = {}

    for routine in routines_list:
        schedule = routine.get("schedule") or []
        if not schedule:
            continue

        routine_id = routine.get("id") or routine.get("name")
        target_weekdays = _extract_weekdays_from_routine(routine)
        if not target_weekdays:
            continue

        for offset in range(days_ahead + 1):
            target_date = today + timedelta(days=offset)
            if target_date.weekday() not in target_weekdays:
                continue

            date_str = target_date.isoformat()
            if date_str not in cached_daily_data:
                cached_daily_data[date_str] = await get_daily_tasks_data(user_id, date_str)

            tasks_for_day = cached_daily_data[date_str].get("tasks", [])
            if _routine_present_in_tasks(tasks_for_day, routine_id, schedule):
                continue

            apply_result = await apply_routine_to_day(user_id, date_str, routine_id, conflict_strategy="merge")
            if apply_result.get("status") == "success":
                cached_daily_data[date_str] = await get_daily_tasks_data(user_id, date_str)
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
    logger.debug(
        "EIXA_DATA | get_all_projects: Attempting to retrieve all projects for user '%s'. Path: users/%s/%s",
        user_id,
        user_id,
        projects_ref.id,
    )
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


async def build_agenda_payload(user_id: str) -> Dict[str, Any]:
    """Retorna estrutura completa da agenda, incluindo microtarefas agregadas por data."""
    logger.debug(f"EIXA_DATA | build_agenda_payload: Building agenda view payload for user '{user_id}'.")

    await ensure_recurring_routines(user_id)

    try:
        agenda_by_date_task = asyncio.create_task(get_all_daily_tasks(user_id))
        unscheduled_task = asyncio.create_task(get_all_unscheduled_tasks(user_id))
        projects_task = asyncio.create_task(get_all_projects(user_id))

        agenda_by_date, unscheduled_tasks, projects = await asyncio.gather(
            agenda_by_date_task,
            unscheduled_task,
            projects_task
        )
    except Exception as exc:
        logger.error(
            f"EIXA_DATA | build_agenda_payload: Failed to fetch agenda base data for user '{user_id}': {exc}",
            exc_info=True
        )
        # fallback to sequential calls to avoid cancelling the coroutine chain
        agenda_by_date = await get_all_daily_tasks(user_id)
        unscheduled_tasks = await get_all_unscheduled_tasks(user_id)
        projects = await get_all_projects(user_id)

    now_iso = datetime.now(timezone.utc).isoformat()

    agenda_by_date = agenda_by_date or {}
    unscheduled_tasks = unscheduled_tasks or []

    microtasks_by_date = defaultdict(list)
    unscheduled_microtasks: List[Dict[str, Any]] = []

    for project in projects or []:
        project_microtasks = project.get("micro_tasks", []) if isinstance(project, dict) else []
        if not isinstance(project_microtasks, list):
            continue

        for micro_task in project_microtasks:
            if not isinstance(micro_task, dict):
                continue

            normalized = _normalize_microtask_for_agenda(project, micro_task, now_iso=now_iso)
            if normalized.get("due_date"):
                microtasks_by_date[normalized["due_date"]].append(normalized)
            else:
                unscheduled_microtasks.append(normalized)

    sorted_microtasks_by_date = {
        date: _sort_microtasks(items) for date, items in microtasks_by_date.items()
    }
    unscheduled_microtasks_sorted = _sort_microtasks(unscheduled_microtasks)

    enriched_by_date: Dict[str, Dict[str, Any]] = {}
    for date_str, day_data in agenda_by_date.items():
        day_copy = dict(day_data) if isinstance(day_data, dict) else {}
        if not isinstance(day_copy.get("tasks"), list):
            day_copy["tasks"] = []
        day_copy["micro_tasks"] = sorted_microtasks_by_date.get(date_str, [])
        enriched_by_date[date_str] = day_copy

    for date_str, items in sorted_microtasks_by_date.items():
        if date_str not in enriched_by_date:
            enriched_by_date[date_str] = {
                "tasks": [],
                "micro_tasks": items
            }

    microtasks_total = sum(len(items) for items in sorted_microtasks_by_date.values()) + len(unscheduled_microtasks_sorted)

    payload = {
        "by_date": enriched_by_date,
        "unscheduled": unscheduled_tasks,
        "micro_tasks_unscheduled": unscheduled_microtasks_sorted,
        "metadata": {
            "micro_tasks_total": microtasks_total
        }
    }

    logger.debug(
        "EIXA_DATA | build_agenda_payload: Built payload for user '%s' | days=%d | unscheduled=%d | microtasks=%d",
        user_id,
        len(enriched_by_date),
        len(unscheduled_tasks or []),
        microtasks_total
    )

    return payload

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