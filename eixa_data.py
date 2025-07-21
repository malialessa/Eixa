import logging
import uuid
from google.cloud import firestore
import asyncio
from datetime import datetime, timedelta, timezone, time

from firestore_client_singleton import _initialize_firestore_client_instance
from config import (
    USERS_COLLECTION, EIXA_INTERACTIONS_COLLECTION,
    EIXA_ROUTINES_COLLECTION, EIXA_GOOGLE_AUTH_COLLECTION,
    SUBCOLLECTIONS_MAP
)
from collections_manager import get_user_subcollection, get_task_doc_ref, get_project_doc_ref, get_top_level_collection
from google_calendar_utils import GoogleCalendarUtils # Mantém o import, ele já está correto

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
        # Se parsed_time é None, retorna uma tupla que coloca a tarefa no início
        # (0, algo) é menor que (1, qualquer_hora)
        if parsed_time:
            return (1, parsed_time)
        return (0, 0) # Coloca tarefas sem tempo no início, sem ordem específica entre elas

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
                    "is_synced_with_google_calendar": False,
                    "created_at": datetime.now(timezone.utc).isoformat(), # NOVO
                    "updated_at": datetime.now(timezone.utc).isoformat() # NOVO
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
                t.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                t.setdefault("updated_at", t.get("updated_at", datetime.now(timezone.utc).isoformat()))
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
        # Garante que as tarefas estejam ordenadas antes de salvar
        if "tasks" in data and isinstance(data["tasks"], list):
            data["tasks"] = _sort_tasks_by_time(data["tasks"])
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
    """
    Busca um template de rotina por ID ou nome.
    Se for um nome, busca na coleção. Se for um ID, busca diretamente.
    """
    db = _initialize_firestore_client_instance()
    routines_ref = db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION)
    
    # Primeiro, tenta buscar por ID
    doc_ref = routines_ref.document(routine_id_or_name)
    doc = await asyncio.to_thread(doc_ref.get)
    if doc.exists:
        logger.info(f"EIXA_DATA | get_routine_template: Routine '{routine_id_or_name}' found by ID for user '{user_id}'.")
        routine_data = doc.to_dict()
        routine_data['id'] = doc.id # Garante que o ID está no dict
        return routine_data
    
    # Se não encontrou por ID, tenta buscar por nome
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
    """
    Salva um template de rotina. Espera que o `data` já inclua `routine_name`, `schedule`, etc.
    E que os itens no `schedule` já tenham `id` e `created_at` (do LLM ou do frontend).
    """
    db = _initialize_firestore_client_instance()
    doc_ref = db.collection(USERS_COLLECTION).document(user_id).collection(EIXA_ROUTINES_COLLECTION).document(routine_id)
    logger.debug(f"EIXA_DATA | save_routine_template: Saving routine '{routine_id}' for user '{user_id}'. Path: {doc_ref.path}. Data: {data}")
    try:
        # Adicionar/Atualizar timestamps da rotina principal
        current_time = datetime.now(timezone.utc).isoformat()
        data.setdefault("created_at", current_time)
        data["updated_at"] = current_time

        # Garante que cada item do schedule tenha 'created_at' e 'updated_at'
        if 'schedule' in data and isinstance(data['schedule'], list):
            for item in data['schedule']:
                item.setdefault("created_at", current_time)
                item["updated_at"] = current_time
                # Opcional: Garanta que tenham um ID, embora o LLM deva gerar
                item.setdefault("id", str(uuid.uuid4()))

        await asyncio.to_thread(doc_ref.set, data)
        logger.info(f"EIXA_DATA | save_routine_template: Routine '{routine_id}' for user '{user_id}' saved successfully.")
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR: Failed to save routine '{routine_id}' for user '{user_id}'. Error: {e}", exc_info=True)
        raise

async def delete_routine_template(user_id: str, routine_id_or_name: str) -> Dict[str, Any]:
    """
    Deleta um template de rotina por ID ou nome.
    """
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
        # new_tasks_for_day começa vazia
    elif conflict_strategy == "merge":
        logger.info(f"EIXA_DATA | apply_routine_to_day: Merging with existing tasks for {date_str}.")
        new_tasks_for_day.extend(existing_tasks)
    else:
        logger.warning(f"EIXA_DATA | apply_routine_to_day: Unknown conflict strategy '{conflict_strategy}'. Defaulting to 'overwrite'.")
        # new_tasks_for_day permanece vazia para overwrite padrão
        
    for routine_item in routine_schedule:
        # Garante que cada item da rotina tem os campos necessários para uma tarefa
        task = {
            "id": routine_item.get("id", str(uuid.uuid4())), # Usa o ID do item da rotina se existir
            "description": routine_item.get("description", "Tarefa da Rotina"),
            "completed": False,
            "time": routine_item.get("time", "00:00"),
            "duration_minutes": routine_item.get("duration_minutes", 0),
            "origin": "routine", # Origem: rotina
            "routine_item_id": routine_item.get("id"), # Referência ao ID do item no template da rotina
            "google_calendar_event_id": None,
            "is_synced_with_google_calendar": False,
            "created_at": routine_item.get("created_at", datetime.now(timezone.utc).isoformat()), # Mantém o created_at da rotina se existir
            "updated_at": datetime.now(timezone.utc).isoformat() # Atualiza updated_at
        }
        
        # Lógica de merge: Se já existe uma tarefa com o MESMO routine_item_id E data, atualiza.
        # Caso contrário, se o tempo for igual e for uma tarefa não-rotina, pode sobrescrever ou adicionar.
        if conflict_strategy == "merge":
            found_existing = False
            for i, existing_task in enumerate(new_tasks_for_day):
                # Se for o MESMO item de rotina para o mesmo dia, atualiza
                if existing_task.get("routine_item_id") == task["routine_item_id"]:
                    new_tasks_for_day[i] = task
                    found_existing = True
                    break
                # Se não for o mesmo item de rotina, mas o tempo for igual E não for um evento GC
                # E não for uma tarefa já marcada como concluída, tratamos como potencial duplicata
                # Para merge, se encontramos um conflito de horário/descrição (excluindo GC events),
                # vamos apenas logar e não adicionar o item da rotina, priorizando o existente.
                # Uma lógica mais complexa perguntaria ao usuário ou tentaria reorganizar.
                if existing_task.get("description", "").lower() == task["description"].lower() and \
                   existing_task.get("time") == task["time"] and \
                   existing_task.get("origin") != "google_calendar" and \
                   existing_task.get("completed") == False:
                    logger.warning(f"EIXA_DATA | apply_routine_to_day: Potential duplicate or time conflict for task '{task['description']}' at '{task['time']}'. Skipping addition for merge strategy.")
                    found_existing = True # Considera como "já tratado"
                    break
            
            if not found_existing:
                new_tasks_for_day.append(task)
        elif conflict_strategy == "overwrite":
             # Para overwrite, sempre adiciona; a lista new_tasks_for_day é criada vazia no início
            new_tasks_for_day.append(task)

        logger.debug(f"EIXA_DATA | apply_routine_to_day: Processed task from routine: {task['description']} at {task['time']}")
    
    try:
        # Salva o estado final da agenda do dia, garantindo que esteja ordenada
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
        # Obtém credenciais
        creds = await google_calendar_utils.get_credentials(user_id)
        if not creds:
            logger.warning(f"EIXA_DATA | sync_google_calendar_events_to_eixa: No Google Calendar credentials found for user {user_id}. Cannot sync.")
            return {"status": "error", "message": "Credenciais do Google Calendar não encontradas. Por favor, conecte sua conta."}

        # CORREÇÃO: Passar o objeto 'creds' para list_calendar_events
        google_events = await google_calendar_utils.list_calendar_events(user_id, start_date_obj, end_date_obj, credentials=creds)
        
        if not google_events:
            logger.info(f"EIXA_DATA | No Google Calendar events found for user {user_id} in the specified period.")
            return {"status": "info", "message": "Nenhum evento do Google Calendar encontrado para o período especificado."}

        added_count = 0
        updated_count = 0

        # Mapeamento para armazenar as tarefas por data_str e economizar escritas no Firestore
        tasks_to_save_by_date = {} # Ex: {'2023-10-26': {'tasks': [...]}}

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
                # Prioriza dateTime (com fuso horário), depois date (dia inteiro, sem hora)
                if start_info.get('dateTime'):
                    event_start_dt = datetime.fromisoformat(start_info['dateTime'])
                    # Se não tem tzinfo, e é dateTime, assume UTC (melhor para consistência de DB)
                    if event_start_dt.tzinfo is None:
                        event_start_dt = event_start_dt.replace(tzinfo=timezone.utc)
                elif start_info.get('date'):
                    is_all_day = True
                    # Eventos de dia inteiro no GC são `date` em vez de `dateTime`.
                    # Para a EIXA, podemos representá-los como tarefas às 00:00 do dia.
                    event_start_dt = datetime.fromisoformat(start_info['date']).replace(tzinfo=timezone.utc) # Assume início do dia em UTC
                
                if end_info and end_info.get('dateTime'):
                    event_end_dt = datetime.fromisoformat(end_info['dateTime'])
                    if event_end_dt.tzinfo is None:
                        event_end_dt = event_end_dt.replace(tzinfo=timezone.utc)
                elif end_info and end_info.get('date') and is_all_day:
                    # Para eventos de dia inteiro, o `end.date` no GC é o dia *seguinte* ao último dia do evento.
                    # Ex: Evento de 1 dia (01/01) -> start.date = 01/01, end.date = 02/01
                    # Então, o final do evento é o final do dia anterior ao end.date.
                    event_end_dt = datetime.fromisoformat(end_info['date']).replace(tzinfo=timezone.utc)
                
                # Se não conseguiu determinar o fim, assume 1 hora
                if not event_end_dt and event_start_dt:
                    event_end_dt = event_start_dt + timedelta(hours=1)
                
                if not event_start_dt or not event_end_dt: raise ValueError("Could not determine start or end datetime for event.")
                
                # Para armazenamento na EIXA, vamos usar a data e hora em UTC para consistência.
                # O frontend pode converter para o fuso horário do usuário para exibição.
                date_str = event_start_dt.strftime('%Y-%m-%d')
                time_str = event_start_dt.strftime('%H:%M')
                
                # Duração em minutos
                duration_minutes = 0
                if not is_all_day:
                    duration_minutes = int((event_end_dt - event_start_dt).total_seconds() / 60)
                else: # Eventos de dia inteiro podem ter duração de 24h ou múltiplos de 24h
                    duration_days = (event_end_dt - event_start_dt).days
                    duration_minutes = duration_days * 24 * 60

                if duration_minutes < 0:
                    logger.warning(f"EIXA_DATA | Google Calendar event {event_id} ({summary}) has negative duration. Setting to 0.")
                    duration_minutes = 0

                # Obtém as tarefas do dia, ou inicializa se for a primeira vez para essa data
                if date_str not in tasks_to_save_by_date:
                    tasks_to_save_by_date[date_str] = await get_daily_tasks_data(user_id, date_str)
                current_daily_tasks = tasks_to_save_by_date[date_str].get("tasks", [])

                # Verifica se o evento já existe na EIXA para evitar duplicação ou para atualizar
                existing_eixa_task_index = next(
                    (i for i, t in enumerate(current_daily_tasks) if t.get('google_calendar_event_id') == event_id),
                    None
                )

                eixa_task = {
                    "id": current_daily_tasks[existing_eixa_task_index].get('id') if existing_eixa_task_index is not None else str(uuid.uuid4()),
                    "description": summary,
                    "completed": False, # Eventos do GC não são "completados" no sentido de uma tarefa
                    "time": time_str,
                    "duration_minutes": duration_minutes,
                    "origin": "google_calendar",
                    "google_calendar_event_id": event_id,
                    "is_synced_with_google_calendar": True,
                    "created_at": current_daily_tasks[existing_eixa_task_index].get('created_at', datetime.now(timezone.utc).isoformat()) if existing_eixa_task_index is not None else datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

                if existing_eixa_task_index is not None:
                    # Atualiza a tarefa existente
                    logger.debug(f"EIXA_DATA | Updating existing EIXA task for GC event {event_id}: {summary}")
                    current_daily_tasks[existing_eixa_task_index] = eixa_task
                    updated_count += 1
                else:
                    # Adiciona nova tarefa
                    logger.debug(f"EIXA_DATA | Adding new EIXA task from GC event {event_id}: {summary}")
                    current_daily_tasks.append(eixa_task)
                    added_count += 1
                
                # Garante que a lista de tarefas no dict por data seja a atualizada
                tasks_to_save_by_date[date_str]["tasks"] = current_daily_tasks

            except ValueError as e:
                logger.error(f"EIXA_DATA | Could not parse date/time for Google Calendar event {event_id} ({summary}): {e}", exc_info=True)
            except Exception as e:
                logger.critical(f"EIXA_DATA | Unexpected error processing Google Calendar event {event_id} ({summary}): {e}", exc_info=True)

        # Salvar todos os documentos de agenda que foram modificados
        for date_str, daily_data in tasks_to_save_by_date.items():
            await save_daily_tasks_data(user_id, date_str, daily_data) # save_daily_tasks_data já ordena

        logger.info(f"EIXA_DATA | Finished syncing Google Calendar events for user {user_id}. Added: {added_count}, Updated: {updated_count}.")
        return {"status": "success", "message": f"Sincronização com Google Calendar concluída! {added_count} novos eventos e {updated_count} atualizados."}
    
    except Exception as e:
        logger.critical(f"EIXA_DATA | CRITICAL ERROR during Google Calendar sync for user {user_id}: {e}", exc_info=True)
        return {"status": "error", "message": "Falha crítica ao sincronizar com o Google Calendar."}


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

            # Normalização de campos, se houver formatos antigos
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