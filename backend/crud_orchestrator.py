import logging
import asyncio
import uuid
import re
from datetime import date, datetime, timezone # datetime e timezone são importantes para created_at
from typing import Dict, Any

# ATENÇÃO: eixa_data.py e collections_manager.py devem estar totalmente async
# FIX: Adicionado get_all_daily_tasks e get_all_projects à importação
# Eixa_data agora espera 'time' e 'duration_minutes'
from eixa_data import (
    get_daily_tasks_data, save_daily_tasks_data, get_project_data, save_project_data, 
    get_all_daily_tasks, get_all_projects,
    save_routine_template, apply_routine_to_day, delete_routine_template, get_all_routines
)
from collections_manager import get_task_doc_ref, get_project_doc_ref
# NÃO DEVE HAVER IMPORTAÇÃO DE crud_orchestrator AQUI (para evitar ciclo).

logger = logging.getLogger(__name__)

# --- Funções CRUD Internas para Tarefas (Task) ---

# MODIFICADO: Adicionados time_str e duration_minutes
async def _create_task_data(user_id: str, date_str: str, description: str, time_str: str = "00:00", duration_minutes: int = 0) -> Dict[str, Any]:
    logger.debug(f"CRUD | Task | _create_task_data: Entered for user '{user_id}', date '{date_str}', desc '{description}', time '{time_str}', duration '{duration_minutes}'") # Novo log
    if not description:
        logger.warning(f"CRUD | Task | Create failed: Description is mandatory for user '{user_id}'.")
        return {"status": "error", "message": "A descrição é obrigatória para criar uma tarefa.", "data": {}}

    task_id = str(uuid.uuid4())
    # MODIFICADO: Incluindo time, duration_minutes E created_at
    new_task = {
        "id": task_id,
        "description": description.strip(),
        "completed": False,
        "status": "todo", # NOVO: Status para Kanban (todo, in_progress, done)
        "time": time_str,
        "duration_minutes": duration_minutes,
        "origin": "user_added", # Por padrão, criada pelo usuário via CRUD
        "routine_item_id": None,
        "google_calendar_event_id": None,
        "is_synced_with_google_calendar": False,
        "created_at": datetime.now(timezone.utc).isoformat() # NOVO: Timestamp de criação
    }

    logger.debug(f"CRUD | Task | _create_task_data: Calling get_daily_tasks_data for '{date_str}'.")
    daily_data = await get_daily_tasks_data(user_id, date_str)
    tasks = daily_data.get("tasks", [])
    logger.debug(f"CRUD | Task | _create_task_data: Current tasks for '{date_str}': {len(tasks)} tasks.")

    # Verifica duplicidade (aprimorado para considerar tempo também se for o caso)
    if any(t.get("description", "").lower() == new_task["description"].lower() and
           t.get("time") == new_task["time"] and # Agora verifica o tempo também para duplicidade
           not t.get("completed") for t in tasks):
        logger.warning(f"CRUD | Task | Duplicate create attempt for '{description}' at '{time_str}' on '{date_str}' for user '{user_id}'.")
        return {"status": "duplicate", "message": f"Tarefa '{description}' às {time_str} já existe para {date_str}.", "data": {}}

    tasks.append(new_task)
    daily_data["tasks"] = tasks

    try:
        logger.debug(f"CRUD | Task | _create_task_data: Calling save_daily_tasks_data for '{date_str}'.")
        await save_daily_tasks_data(user_id, date_str, daily_data)
        logger.info(f"CRUD | Task | Task '{description}' created with ID '{task_id}' on '{date_str}' at '{time_str}' for user '{user_id}'. Data saved successfully to Firestore.")
        agenda_data = await get_all_daily_tasks(user_id)
        return {"status": "success", "message": f"Tarefa '{description}' adicionada para {date_str} às {time_str}.", "data": {"task_id": task_id}, "html_view_data": {"agenda": agenda_data}}
    except Exception as e:
        logger.critical(f"CRUD | Task | CRITICAL ERROR: Failed to write task to Firestore for user '{user_id}' on '{date_str}'. Payload: {daily_data}. Error: {e}", exc_info=True)
        return {"status": "error", "message": "Falha ao salvar a tarefa no banco de dados.", "data": {}, "debug": str(e)}

# MODIFICADO: Adicionados new_time e new_duration
async def _update_task_status_or_data(user_id: str, date_str: str, task_id: str, new_completed_status: bool = None, new_description: str = None, new_time: str = None, new_duration_minutes: int = None, new_status: str = None) -> Dict[str, Any]:
    logger.debug(f"CRUD | Task | _update_task_status_or_data: Entered for user '{user_id}', task_id '{task_id}'. New Desc: '{new_description}', New Time: '{new_time}', New Duration: '{new_duration_minutes}', New Status: '{new_status}'.")
    daily_data = await get_daily_tasks_data(user_id, date_str)
    tasks = daily_data.get("tasks", [])
    task_found = False

    for task in tasks:
        if task.get("id") == task_id:
            if new_completed_status is not None:
                task["completed"] = new_completed_status
                # Sync status string if completed bool changes
                if new_completed_status:
                    task["status"] = "done"
                elif task.get("status") == "done":
                    task["status"] = "todo"

            if new_description is not None:
                task["description"] = new_description.strip()
            # MODIFICADO: Atualizando tempo e duração
            if new_time is not None:
                task["time"] = new_time
            if new_duration_minutes is not None:
                task["duration_minutes"] = new_duration_minutes
            
            # NOVO: Atualizando status string
            if new_status is not None:
                task["status"] = new_status
                # Sync completed bool if status string changes
                if new_status == "done":
                    task["completed"] = True
                else:
                    task["completed"] = False
            
            # Adicionado: Atualiza updated_at (boa prática para qualquer modificação)
            task["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            task_found = True
            break

    if task_found:
        try:
            logger.debug(f"CRUD | Task | _update_task_status_or_data: Calling save_daily_tasks_data for '{date_str}'.")
            await save_daily_tasks_data(user_id, date_str, daily_data)
            logger.info(f"CRUD | Task | Task ID '{task_id}' on '{date_str}' updated for user '{user_id}'. Data updated successfully to Firestore.")
            agenda_data = await get_all_daily_tasks(user_id)
            return {"status": "success", "message": "Tarefa atualizada com sucesso.", "html_view_data": {"agenda": agenda_data}} 
        except Exception as e:
            logger.error(f"CRUD | Task | Failed to update task in Firestore for user '{user_id}': {e}", exc_info=True)
            return {"status": "error", "message": "Não foi possível atualizar a tarefa."}

    logger.warning(f"CRUD | Task | Update failed: Task ID '{task_id}' not found on '{date_str}' for user '{user_id}'.")
    return {"status": "error", "message": "Não foi possível encontrar a tarefa para atualização."}

async def _delete_task_by_id(user_id: str, date_str: str, task_id: str) -> Dict[str, Any]:
    logger.debug(f"CRUD | Task | _delete_task_by_id: Entered for user '{user_id}', task_id '{task_id}'.")
    daily_data = await get_daily_tasks_data(user_id, date_str)
    tasks = daily_data.get("tasks", [])
    original_len = len(tasks)

    tasks = [t for t in tasks if t.get("id") != task_id]

    if len(tasks) < original_len:
        agenda_doc_ref = get_task_doc_ref(user_id, date_str)

        try:
            if not tasks:
                logger.debug(f"CRUD | Task | Attempting to delete empty agenda doc at: {agenda_doc_ref.path} for user '{user_id}'.")
                # Se não houver mais tarefas para o dia, deleta o documento do dia inteiro
                await asyncio.to_thread(agenda_doc_ref.delete)
                logger.info(f"CRUD | Task | Agenda document for '{date_str}' deleted as it became empty for user '{user_id}'.")
            else:
                daily_data["tasks"] = tasks
                logger.debug(f"CRUD | Task | _delete_task_by_id: Calling save_daily_tasks_data for '{date_str}'.")
                await save_daily_tasks_data(user_id, date_str, daily_data)
                logger.info(f"CRUD | Task | Task ID '{task_id}' on '{date_str}' deleted for user '{user_id}'. Agenda updated.")
            agenda_data = await get_all_daily_tasks(user_id)
            return {"status": "success", "message": "Tarefa excluída com sucesso.", "html_view_data": {"agenda": agenda_data}} 
        except Exception as e:
            logger.error(f"CRUD | Task | Failed to delete/update agenda document for user '{user_id}' on '{date_str}': {e}", exc_info=True)
            return {"status": "error", "message": "Não foi possível excluir a tarefa."}

    logger.warning(f"CRUD | Task | Delete failed: Task ID '{task_id}' not found for deletion on '{date_str}' for user '{user_id}'.")
    return {"status": "error", "message": "Não foi possível encontrar a tarefa para exclusão."}

async def _bulk_delete_tasks(user_id: str, tasks_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Exclui várias tarefas em lote. Suporta:
    1. Lista explícita: data: {"tasks": [{"task_id": "..", "date": "YYYY-MM-DD"}, ...]}
    2. Filtros: description_contains, date_before, date_range_start, date_range_end.
    """
    explicit_list = tasks_payload.get('tasks')
    deleted = []
    failed = []
    changed_days = set()

    if explicit_list and isinstance(explicit_list, list):
        for item in explicit_list:
            t_id = item.get('task_id') or item.get('id')
            d_str = item.get('date')
            if not (t_id and d_str):
                failed.append({"task_id": t_id, "date": d_str, "reason": "missing_id_or_date"})
                continue
            daily_data = await get_daily_tasks_data(user_id, d_str)
            tasks = daily_data.get('tasks', [])
            new_tasks = [t for t in tasks if t.get('id') != t_id]
            if len(new_tasks) == len(tasks):
                failed.append({"task_id": t_id, "date": d_str, "reason": "not_found"})
                continue
            if new_tasks:
                daily_data['tasks'] = new_tasks
                await save_daily_tasks_data(user_id, d_str, daily_data)
            else:
                # delete doc if empty
                agenda_doc_ref = get_task_doc_ref(user_id, d_str)
                await asyncio.to_thread(agenda_doc_ref.delete)
            deleted.append({"task_id": t_id, "date": d_str})
            changed_days.add(d_str)
    else:
        # Filter-based deletion
        desc_contains = tasks_payload.get('description_contains')
        date_before = tasks_payload.get('date_before')
        range_start = tasks_payload.get('date_range_start')
        range_end = tasks_payload.get('date_range_end')
        agenda_all = await get_all_daily_tasks(user_id)
        for d_str, day_data in agenda_all.items():
            try:
                day_date = date.fromisoformat(d_str)
            except Exception:
                continue
            if date_before:
                try:
                    if day_date >= date.fromisoformat(date_before):
                        continue
                except Exception:
                    pass
            if range_start and range_end:
                try:
                    if not (date.fromisoformat(range_start) <= day_date <= date.fromisoformat(range_end)):
                        continue
                except Exception:
                    pass
            tasks = day_data.get('tasks', [])
            keep = []
            removed_here = []
            for t in tasks:
                remove = False
                if desc_contains and isinstance(t.get('description'), str):
                    if desc_contains.lower() in t['description'].lower():
                        remove = True
                if remove:
                    removed_here.append(t)
                else:
                    keep.append(t)
            if removed_here:
                if keep:
                    day_data['tasks'] = keep
                    await save_daily_tasks_data(user_id, d_str, day_data)
                else:
                    agenda_doc_ref = get_task_doc_ref(user_id, d_str)
                    await asyncio.to_thread(agenda_doc_ref.delete)
                for t in removed_here:
                    deleted.append({"task_id": t.get('id'), "date": d_str})
                changed_days.add(d_str)

    agenda_data = await get_all_daily_tasks(user_id)
    return {
        "status": "success" if deleted else "no_changes",
        "message": f"{len(deleted)} tarefas excluídas." if deleted else "Nenhuma tarefa correspondia aos critérios.",
        "data": {"deleted_count": len(deleted), "failed": failed},
        "html_view_data": {"agenda": agenda_data}
    }

# --- Funções CRUD Internas para Projetos (Project) ---

ALLOWED_PROJECT_UPDATE_FIELDS = {"name", "description", "progress_tags", "deadline", "micro_tasks", "status", "completion_percentage", "expected_energy_level", "priority", "impact_level", "category", "sub_category", "associated_goals", "dependencies", "related_projects", "stakeholders", "notes", "custom_tags"}

async def _create_project_data(user_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
    logger.debug(f"CRUD | Project | _create_project_data: Entered for user '{user_id}'. Project name: '{project_data.get('name')}'")
    if not project_data.get("name"):
        logger.warning(f"CRUD | Project | Create failed: Project name is mandatory for user '{user_id}'.")
        return {"status": "error", "message": "O nome do projeto é obrigatório.", "data": {}}

    project_id = str(uuid.uuid4())
    
    description_value = project_data.get("description")
    normalized_description = description_value.strip() if isinstance(description_value, str) else ""

    new_project = {
        "id": project_id,
        "user_id": user_id,
        "name": project_data.get("name", "").strip(),
        "description": normalized_description,
        "status": project_data.get("status", "open"),
        "progress_tags": project_data.get("progress_tags", ["iniciado"]),
        "completion_percentage": project_data.get("completion_percentage", 0),
        "created_at": datetime.now(timezone.utc).isoformat(), # NOVO: Timestamp de criação
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "deadline": project_data.get("deadline"),
        "completed_at": None,
        "expected_energy_level": project_data.get("expected_energy_level", "médio"),
        "expected_time_commitment": project_data.get("expected_time_commitment", "variável"),
        "actual_time_spent": 0,
        "priority": project_data.get("priority", "média"),
        "impact_level": project_data.get("impact_level", "médio"),
        "micro_tasks": project_data.get("micro_tasks", []),
        "category": project_data.get("category", "pessoal"),
        "sub_category": project_data.get("sub_category", ""),
        "associated_goals": project_data.get("associated_goals", []),
        "dependencies": project_data.get("dependencies", []),
        "related_projects": project_data.get("related_projects", []),
        "stakeholders": project_data.get("stakeholders", []),
        "notes": project_data.get("notes", ""),
        "custom_tags": project_data.get("custom_tags", []),
        "last_review_date": None,
        "review_notes": None,
        "next_review_date": None,
        "history": []
    }

    try:
        logger.debug(f"CRUD | Project | _create_project_data: Calling save_project_data for project '{project_id}'.")
        await save_project_data(user_id, project_id, new_project)

        logger.info(f"CRUD | Project | Project '{new_project['name']}' created with ID '{project_id}' for user '{user_id}'. Data saved successfully to Firestore.")
        projects_data = await get_all_projects(user_id)
        return {"status": "success", "message": f"Projeto '{new_project['name']}' criado!", "data": {"project_id": project_id}, "html_view_data": {"projetos": projects_data}}
    except Exception as e:
        logger.critical(f"CRUD | Project | CRITICAL ERROR: Failed to write project to Firestore for user '{user_id}' with data {new_project}: {e}", exc_info=True)
        return {"status": "error", "message": "Falha ao salvar o projeto no banco de dados.", "data": {}, "debug": str(e)}

async def _update_project_data(user_id: str, project_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    logger.debug(f"CRUD | Project | _update_project_data: Entered for user '{user_id}', project_id '{project_id}'.")
    if not all(key in ALLOWED_PROJECT_UPDATE_FIELDS for key in updates.keys()):
        invalid_fields = [key for key in updates.keys() if key not in ALLOWED_PROJECT_UPDATE_FIELDS]
        logger.warning(f"CRUD | Project | Update attempt for '{project_id}' with invalid fields: {invalid_fields} for user '{user_id}'.")
        return {"status": "error", "message": f"Campos inválidos para atualização: {', '.join(invalid_fields)}"}

    current_project_data = await get_project_data(user_id, project_id)

    if current_project_data:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        if "description" in updates:
            description_value = updates.get("description")
            updates["description"] = description_value.strip() if isinstance(description_value, str) else ""

        current_project_data.update(updates)

        if "status" in updates and updates["status"] == "completed":
            current_project_data["completed_at"] = datetime.now(timezone.utc).isoformat()
        elif "status" in updates and updates["status"] != "completed" and "completed_at" in current_project_data:
            current_project_data["completed_at"] = None

        try:
            logger.debug(f"CRUD | Project | _update_project_data: Calling save_project_data for project '{project_id}'.")
            await save_project_data(user_id, project_id, current_project_data)
            logger.info(f"CRUD | Project | Project '{project_id}' updated for user '{user_id}'. Changes: {list(updates.keys())}. Data updated successfully to Firestore.")
            projects_data = await get_all_projects(user_id)
            return {"status": "success", "message": "Projeto atualizado com sucesso.", "html_view_data": {"projetos": projects_data}} 
        except Exception as e:
            logger.error(f"CRUD | Project | Failed to update project in Firestore for user '{user_id}': {e}", exc_info=True)
            return {"status": "error", "message": "Não foi possível atualizar o projeto."}

    logger.warning(f"CRUD | Project | Update failed: Project ID '{project_id}' not found for user '{user_id}'.")
    return {"status": "error", "message": "Não foi possível encontrar o projeto para atualização."}

async def _delete_project_fully(user_id: str, project_id: str) -> Dict[str, Any]:
    logger.debug(f"CRUD | Project | _delete_project_fully: Entered for user '{user_id}', project_id '{project_id}'.")
    project_doc_ref = get_project_doc_ref(user_id, project_id)

    if await get_project_data(user_id, project_id):
        try:
            logger.debug(f"CRUD | Project | Attempting to delete project doc at: {project_doc_ref.path} for user '{user_id}'.")
            await asyncio.to_thread(project_doc_ref.delete)
            logger.info(f"CRUD | Project | Project '{project_id}' deleted for user '{user_id}'.")
            projects_data = await get_all_projects(user_id)
            return {"status": "success", "message": "Projeto excluído com sucesso.", "html_view_data": {"projetos": projects_data}} 
        except Exception as e:
            logger.error(f"CRUD | Project | Failed to delete project document '{project_id}' for user '{user_id}': {e}", exc_info=True)
            return {"status": "error", "message": "Não foi possível excluir o projeto."}
    else:
        logger.warning(f"CRUD | Project | Delete failed: Project ID '{project_id}' not found for user '{user_id}'.")
        return {"status": "error", "message": "Não foi possível encontrar o projeto para exclusão."}

# --- Funções CRUD Internas para Rotinas (Routine) ---

async def _create_routine(user_id: str, routine_data: Dict[str, Any]) -> Dict[str, Any]:
    logger.debug(f"CRUD | Routine | _create_routine: Entered for user '{user_id}'. Name: '{routine_data.get('name')}'")
    if not routine_data.get("name"):
        return {"status": "error", "message": "O nome da rotina é obrigatório."}
    
    # Ensure ID
    if not routine_data.get("id"):
        routine_data["id"] = str(uuid.uuid4())
    
    try:
        await save_routine_template(user_id, routine_data)
        routines = await get_all_routines(user_id)
        return {"status": "success", "message": "Rotina criada com sucesso!", "html_view_data": {"routines": routines}}
    except Exception as e:
        logger.error(f"CRUD | Routine | Failed to create routine: {e}", exc_info=True)
        return {"status": "error", "message": f"Erro ao criar rotina: {str(e)}"}

async def _apply_routine(user_id: str, routine_id: str, date_str: str) -> Dict[str, Any]:
    logger.debug(f"CRUD | Routine | _apply_routine: Applying '{routine_id}' to '{date_str}' for user '{user_id}'")
    try:
        await apply_routine_to_day(user_id, routine_id, date_str)
        agenda_data = await get_all_daily_tasks(user_id)
        return {"status": "success", "message": "Rotina aplicada com sucesso!", "html_view_data": {"agenda": agenda_data}}
    except Exception as e:
        logger.error(f"CRUD | Routine | Failed to apply routine: {e}", exc_info=True)
        return {"status": "error", "message": f"Erro ao aplicar rotina: {str(e)}"}

async def _delete_routine(user_id: str, routine_id: str) -> Dict[str, Any]:
    try:
        await delete_routine_template(user_id, routine_id)
        routines = await get_all_routines(user_id)
        return {"status": "success", "message": "Rotina excluída com sucesso!", "html_view_data": {"routines": routines}}
    except Exception as e:
        logger.error(f"CRUD | Routine | Failed to delete routine: {e}", exc_info=True)
        return {"status": "error", "message": f"Erro ao excluir rotina: {str(e)}"}

# --- Orquestrador Principal de Ações CRUD (Chamado pelo Frontend) ---
async def orchestrate_crud_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Processa ações CRUD vindas do frontend ou do orquestrador principal (LLM).
    Valida o payload e roteia a ação para as funções CRUD internas apropriadas.
    Payload esperado:
    {
      "user_id": str,
      "item_type": "task" | "project",
      "action": "create" | "update" | "delete",
      "data": { 
          "description": str,
          "date": "YYYY-MM-DD",
          "time": "HH:MM", 
          "duration_minutes": int,
          "completed": bool, # Para updates de status
          ...outros dados específicos... 
      }, 
      "item_id": str (opcional para 'create', obrigatório para 'update'/'delete')
    }
    """
    logger.debug(f"CRUD_ORCHESTRATOR_ENTERED | Payload received: {payload}") 

    user_id = payload.get('user_id')
    # Normaliza item_type e action para evitar falhas por espaços ou capitalização
    raw_item_type = payload.get('item_type')
    item_type = raw_item_type.strip().lower() if isinstance(raw_item_type, str) else raw_item_type
    raw_action = payload.get('action')
    action = raw_action.strip().lower() if isinstance(raw_action, str) else raw_action
    data = payload.get('data', {}) 
    item_id = payload.get('item_id')

    debug_info = {"user_id": user_id, "invoked_action": f"{item_type}_{action}", "item_id": item_id}
    logger.info(f"CRUD | orchestrate_crud_action received: {debug_info}, data: {data}") 

    if not all([user_id, item_type, action]):
        logger.error(f"CRUD | orchestrate_crud_action: Missing required payload fields. Payload: {payload}")
        return {"status": "error", "message": "user_id, item_type e action são obrigatórios.", "data": {}, "debug_info": debug_info}

    try:
        if item_type == 'task':
            logger.debug(f"CRUD | orchestrate_crud_action: Task action '{action}' detected.") 
            date_str = data.get('date') 
            
            time_str = data.get('time', "00:00") 
            duration_minutes = data.get('duration_minutes', 0) 

            is_bulk_delete = (action == 'bulk_delete')

            if not is_bulk_delete and not date_str:
                logger.error(f"CRUD | Task | Missing date for action '{action}' for user '{user_id}'. Payload data: {data}")
                return {"status": "error", "message": "A data é obrigatória para operações de tarefa.", "data": {}, "debug_info": debug_info}

            if not is_bulk_delete:
                try:
                    date.fromisoformat(date_str)
                    # Adicionado validação mais rigorosa para o formato de hora
                    if time_str is not None: # Pode ser None se não for relevante para a ação, ex: update só de description
                        if not isinstance(time_str, str) or not re.match(r'^(?:2[0-3]|[01]?[0-9]):(?:[0-5]?[0-9])$', time_str):
                            raise ValueError(f"Formato de hora inválido: '{time_str}'. Use HH:MM (ex: '09:00' ou '14:30').")
                    if duration_minutes is not None:
                        if not isinstance(duration_minutes, int) or duration_minutes < 0:
                            raise ValueError(f"Duração inválida: '{duration_minutes}'. Deve ser um número inteiro positivo.")

                except (ValueError, TypeError) as e:
                    logger.error(f"CRUD | Task | Invalid date/time/duration format for user '{user_id}': {e}. Payload data: {data}", exc_info=True)
                    return {"status": "error", "message": f"Formato de data/hora/duração inválido: {e}. Data: {date_str}, Hora: {time_str}, Duração: {duration_minutes}.", "data": {}, "debug_info": debug_info}


            if action == 'create':
                description = data.get('description') 
                if not description:
                    logger.error(f"CRUD | Task | Create failed: Description is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "A descrição é obrigatória para criar uma tarefa.", "data": {}, "debug_info": debug_info}
                return await _create_task_data(user_id, date_str, description, time_str, duration_minutes)

            elif action == 'update':
                if not item_id:
                    logger.error(f"CRUD | Task | Update failed: Task ID is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "O ID da tarefa é obrigatório.", "data": {}, "debug_info": debug_info}
                
                # Campos opcionais para update, se não fornecidos, serão None e a função interna os ignora
                new_completed_status = data.get('completed')
                new_description = data.get('description')
                new_time = data.get('time')
                new_duration_minutes = data.get('duration_minutes')

                return await _update_task_status_or_data(user_id, date_str, item_id, 
                                                           new_completed_status, 
                                                           new_description,
                                                           new_time,
                                                           new_duration_minutes)

            elif action == 'delete':
                if not item_id:
                    logger.error(f"CRUD | Task | Delete failed: Task ID is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "O ID da tarefa é obrigatório.", "data": {}, "debug_info": debug_info}
                return await _delete_task_by_id(user_id, date_str, item_id)
            
            elif is_bulk_delete:
                # bulk delete não exige date_str global; usa datas internas ou filtros
                logger.debug(f"CRUD | Task | Bulk delete acionado. Data global ignorada. Payload filtros: {data}")
                return await _bulk_delete_tasks(user_id, data)
            
            else: # Ação desconhecida
                logger.error(f"CRUD | Task | Unknown action '{action}' for task type for user '{user_id}'. Payload data: {data}")
                return {"status": "error", "message": "Ação de tarefa não reconhecida.", "data": {}, "debug_info": debug_info}

        elif item_type == 'project':
            logger.debug(f"CRUD | orchestrate_crud_action: Project action '{action}' detected.") 
            if action == 'create':
                project_name = data.get("name") 
                if not project_name:
                    logger.error(f"CRUD | Project | Create failed: Project name is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "O nome do projeto é obrigatório.", "data": {}, "debug_info": debug_info}
                return await _create_project_data(user_id, data) 

            elif action == 'update':
                if not item_id:
                    logger.error(f"CRUD | Project | Update failed: Project ID is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "O ID do projeto é obrigatório.", "data": {}, "debug_info": debug_info}
                return await _update_project_data(user_id, item_id, data) 

            elif action == 'delete':
                if not item_id:
                    logger.error(f"CRUD | Project | Delete failed: Project ID is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "O ID do projeto é obrigatório.", "data": {}, "debug_info": debug_info}
                return await _delete_project_fully(user_id, item_id)
            
            else: # Ação desconhecida
                logger.error(f"CRUD | Project | Unknown action '{action}' for project type for user '{user_id}'. Payload data: {data}")
                return {"status": "error", "message": "Ação de projeto não reconhecida.", "data": {}, "debug_info": debug_info}

        elif item_type == 'routine':
            logger.debug(f"CRUD | orchestrate_crud_action: Routine action '{action}' detected.")
            if action == 'create':
                return await _create_routine(user_id, data)
            elif action == 'apply_routine':
                if not item_id or not data.get('date'): # date is passed in data usually, but let's check payload structure
                    # Frontend sends: item_id=routineId, date=... in payload root or data?
                    # Frontend: payload = { ..., item_id: routineId, date: ... }
                    # orchestrate_crud_action extracts 'date' from 'data' usually?
                    # Let's check how I implemented frontend:
                    # payload = { ..., item_id: routineId, date: ... }
                    # But orchestrate_crud_action does: date_str = data.get('date') for tasks.
                    # For routines, let's look at payload again.
                    pass
                
                # Re-reading frontend implementation:
                # payload = { request_type: 'crud_action', item_type: 'routine', action: 'apply_routine', item_id: routineId, date: ... }
                # orchestrate_crud_action: user_id = payload.get('user_id'), item_type = payload.get('item_type'), action = payload.get('action'), data = payload.get('data', {}), item_id = payload.get('item_id')
                # So 'date' is in payload root? No, wait.
                # Frontend: date: new Date()...
                # Backend orchestrate_crud_action:
                # date_str = data.get('date') is used for tasks.
                # But for apply_routine, the date might be in payload directly or data.
                # Let's assume I should put it in 'data' in frontend or handle it here.
                # In frontend I did: date: ... (at root level of payload object)
                # But orchestrate_crud_action does NOT extract 'date' from root payload, only 'data', 'item_id', 'user_id', 'item_type', 'action'.
                
                # FIX: I should check payload.get('date') as well.
                target_date = payload.get('date') or data.get('date')
                if not target_date:
                     return {"status": "error", "message": "Data é obrigatória para aplicar rotina."}
                return await _apply_routine(user_id, item_id, target_date)

            elif action == 'delete':
                if not item_id:
                    return {"status": "error", "message": "ID da rotina obrigatório."}
                return await _delete_routine(user_id, item_id)
            else:
                return {"status": "error", "message": "Ação de rotina desconhecida."}

        logger.error(f"CRUD | Unknown item type: '{item_type}' for user '{user_id}'. Payload: {payload}")
        return {"status": "error", "message": "Tipo de item não reconhecido.", "data": {}, "debug_info": debug_info}

    except Exception as e:
        logger.critical(f"CRUD | CRITICAL ERROR: Unexpected error in orchestrate_crud_action for user '{user_id}'. Payload: {payload}: {e}", exc_info=True) 
        return {"status": "error", "message": "Ocorreu um erro interno inesperado ao processar a ação CRUD.", "data": {}, "debug_info": {**debug_info, "exception": str(e)}}