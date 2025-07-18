import logging
import asyncio
import uuid
from datetime import date, datetime, timezone
from typing import Dict, Any

# ATENÇÃO: eixa_data.py e collections_manager.py devem estar totalmente async
from eixa_data import get_daily_tasks_data, save_daily_tasks_data, get_project_data, save_project_data
from collections_manager import get_task_doc_ref, get_project_doc_ref
# NÃO DEVE HAVER IMPORTAÇÃO DE crud_orchestrator AQUI (para evitar ciclo).

logger = logging.getLogger(__name__)

# --- Funções CRUD Internas para Tarefas (Task) ---

async def _create_task_data(user_id: str, date_str: str, description: str) -> Dict[str, Any]:
    logger.debug(f"CRUD | Task | _create_task_data: Entered for user '{user_id}', date '{date_str}', desc '{description}'") # Novo log
    if not description:
        logger.warning(f"CRUD | Task | Create failed: Description is mandatory for user '{user_id}'.")
        return {"status": "error", "message": "A descrição é obrigatória para criar uma tarefa.", "data": {}}

    task_id = str(uuid.uuid4())
    new_task = {"id": task_id, "description": description.strip(), "completed": False}

    # get_daily_tasks_data é async, então await diretamente
    logger.debug(f"CRUD | Task | _create_task_data: Calling get_daily_tasks_data for '{date_str}'.") # Novo log
    daily_data = await get_daily_tasks_data(user_id, date_str)
    tasks = daily_data.get("tasks", [])
    logger.debug(f"CRUD | Task | _create_task_data: Current tasks for '{date_str}': {tasks}") # Novo log

    if any(t.get("description", "").lower() == new_task["description"].lower() and not t.get("completed") for t in tasks):
        logger.warning(f"CRUD | Task | Duplicate create attempt for '{description}' on '{date_str}' for user '{user_id}'.")
        return {"status": "duplicate", "message": f"Tarefa '{description}' já existe para {date_str}.", "data": {}}

    tasks.append(new_task)
    daily_data["tasks"] = tasks

    try:
        # save_daily_tasks_data é async, então await diretamente
        logger.debug(f"CRUD | Task | _create_task_data: Calling save_daily_tasks_data for '{date_str}'.") # Novo log
        await save_daily_tasks_data(user_id, date_str, daily_data)
        logger.info(f"CRUD | Task | Task '{description}' created with ID '{task_id}' on '{date_str}' for user '{user_id}'. Data saved successfully to Firestore.")
        # Incluir html_view_data na resposta de sucesso para o frontend atualizar instantaneamente
        agenda_data = await get_all_daily_tasks(user_id) # Buscar a agenda atualizada
        return {"status": "success", "message": f"Tarefa '{description}' adicionada para {date_str}.", "data": {"task_id": task_id}, "html_view_data": {"agenda": agenda_data}}
    except Exception as e:
        logger.critical(f"CRUD | Task | CRITICAL ERROR: Failed to write task to Firestore for user '{user_id}' on '{date_str}'. Payload: {daily_data}. Error: {e}", exc_info=True)
        return {"status": "error", "message": "Falha ao salvar a tarefa no banco de dados.", "data": {}, "debug": str(e)}

async def _update_task_status_or_data(user_id: str, date_str: str, task_id: str, new_completed_status: bool = None, new_description: str = None) -> Dict[str, Any]: # Modificado para retornar dict
    logger.debug(f"CRUD | Task | _update_task_status_or_data: Entered for user '{user_id}', task_id '{task_id}'.") # Novo log
    daily_data = await get_daily_tasks_data(user_id, date_str)
    tasks = daily_data.get("tasks", [])
    task_found = False

    for task in tasks:
        if task.get("id") == task_id:
            if new_completed_status is not None:
                task["completed"] = new_completed_status
            if new_description is not None:
                task["description"] = new_description.strip()
            task_found = True
            break

    if task_found:
        try:
            logger.debug(f"CRUD | Task | _update_task_status_or_data: Calling save_daily_tasks_data for '{date_str}'.") # Novo log
            await save_daily_tasks_data(user_id, date_str, daily_data)
            logger.info(f"CRUD | Task | Task ID '{task_id}' on '{date_str}' updated for user '{user_id}'. Data updated successfully to Firestore.")
            agenda_data = await get_all_daily_tasks(user_id) # Buscar a agenda atualizada
            return {"status": "success", "message": "Tarefa atualizada com sucesso.", "html_view_data": {"agenda": agenda_data}} # Retornar dict
        except Exception as e:
            logger.error(f"CRUD | Task | Failed to update task in Firestore for user '{user_id}': {e}", exc_info=True)
            return {"status": "error", "message": "Não foi possível atualizar a tarefa."} # Retornar dict

    logger.warning(f"CRUD | Task | Update failed: Task ID '{task_id}' not found on '{date_str}' for user '{user_id}'.")
    return {"status": "error", "message": "Não foi possível encontrar a tarefa para atualização."} # Retornar dict

async def _delete_task_by_id(user_id: str, date_str: str, task_id: str) -> Dict[str, Any]: # Modificado para retornar dict
    logger.debug(f"CRUD | Task | _delete_task_by_id: Entered for user '{user_id}', task_id '{task_id}'.") # Novo log
    daily_data = await get_daily_tasks_data(user_id, date_str)
    tasks = daily_data.get("tasks", [])
    original_len = len(tasks)

    tasks = [t for t in tasks if t.get("id") != task_id]

    if len(tasks) < original_len:
        agenda_doc_ref = get_task_doc_ref(user_id, date_str)

        try:
            if not tasks:
                logger.debug(f"CRUD | Task | Attempting to delete empty agenda doc at: {agenda_doc_ref.path} for user '{user_id}'.")
                await asyncio.to_thread(agenda_doc_ref.delete)
                logger.info(f"CRUD | Task | Agenda document for '{date_str}' deleted as it became empty for user '{user_id}'.")
            else:
                daily_data["tasks"] = tasks
                logger.debug(f"CRUD | Task | _delete_task_by_id: Calling save_daily_tasks_data for '{date_str}'.") # Novo log
                await save_daily_tasks_data(user_id, date_str, daily_data)
                logger.info(f"CRUD | Task | Task ID '{task_id}' on '{date_str}' deleted for user '{user_id}'. Agenda updated.")
            agenda_data = await get_all_daily_tasks(user_id) # Buscar a agenda atualizada
            return {"status": "success", "message": "Tarefa excluída com sucesso.", "html_view_data": {"agenda": agenda_data}} # Retornar dict
        except Exception as e:
            logger.error(f"CRUD | Task | Failed to delete/update agenda document for user '{user_id}' on '{date_str}': {e}", exc_info=True)
            return {"status": "error", "message": "Não foi possível excluir a tarefa."} # Retornar dict

    logger.warning(f"CRUD | Task | Delete failed: Task ID '{task_id}' not found for deletion on '{date_str}' for user '{user_id}'.")
    return {"status": "error", "message": "Não foi possível encontrar a tarefa para exclusão."} # Retornar dict

# --- Funções CRUD Internas para Projetos (Project) ---

ALLOWED_PROJECT_UPDATE_FIELDS = {"name", "description", "progress_tags", "deadline", "micro_tasks", "status", "completion_percentage", "expected_energy_level", "priority", "impact_level", "category", "sub_category", "associated_goals", "dependencies", "related_projects", "stakeholders", "notes", "custom_tags"}

async def _create_project_data(user_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
    logger.debug(f"CRUD | Project | _create_project_data: Entered for user '{user_id}'. Project name: '{project_data.get('name')}'") # Novo log
    if not project_data.get("name"):
        logger.warning(f"CRUD | Project | Create failed: Project name is mandatory for user '{user_id}'.")
        return {"status": "error", "message": "O nome do projeto é obrigatório.", "data": {}}

    project_id = str(uuid.uuid4())
    
    # FIX: Ensure description is a string before stripping, handle None explicitly
    description_value = project_data.get("description")
    normalized_description = description_value.strip() if isinstance(description_value, str) else ""

    new_project = {
        "id": project_id,
        "user_id": user_id,
        "name": project_data.get("name", "").strip(),
        "description": normalized_description, # Use a descrição normalizada
        "status": project_data.get("status", "open"),
        "progress_tags": project_data.get("progress_tags", ["iniciado"]),
        "completion_percentage": project_data.get("completion_percentage", 0),
        "created_at": datetime.now(timezone.utc).isoformat(),
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
        logger.debug(f"CRUD | Project | _create_project_data: Calling save_project_data for project '{project_id}'.") # Novo log
        await save_project_data(user_id, project_id, new_project)

        logger.info(f"CRUD | Project | Project '{new_project['name']}' created with ID '{project_id}' for user '{user_id}'. Data saved successfully to Firestore.")
        projects_data = await get_all_projects(user_id) # Buscar projetos atualizados
        return {"status": "success", "message": f"Projeto '{new_project['name']}' criado!", "data": {"project_id": project_id}, "html_view_data": {"projetos": projects_data}}
    except Exception as e:
        logger.critical(f"CRUD | Project | CRITICAL ERROR: Failed to write project to Firestore for user '{user_id}' with data {new_project}: {e}", exc_info=True)
        return {"status": "error", "message": "Falha ao salvar o projeto no banco de dados.", "data": {}, "debug": str(e)}

async def _update_project_data(user_id: str, project_id: str, updates: Dict[str, Any]) -> Dict[str, Any]: # Modificado para retornar dict
    logger.debug(f"CRUD | Project | _update_project_data: Entered for user '{user_id}', project_id '{project_id}'.") # Novo log
    if not all(key in ALLOWED_PROJECT_UPDATE_FIELDS for key in updates.keys()):
        invalid_fields = [key for key in updates.keys() if key not in ALLOWED_PROJECT_UPDATE_FIELDS]
        logger.warning(f"CRUD | Project | Update attempt for '{project_id}' with invalid fields: {invalid_fields} for user '{user_id}'.")
        return {"status": "error", "message": f"Campos inválidos para atualização: {', '.join(invalid_fields)}"} # Retornar dict

    current_project_data = await get_project_data(user_id, project_id)

    if current_project_data:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        # FIX: Handle 'description' updates to ensure it's a string or empty string, not None
        if "description" in updates:
            description_value = updates.get("description")
            updates["description"] = description_value.strip() if isinstance(description_value, str) else ""

        current_project_data.update(updates)

        if "status" in updates and updates["status"] == "completed":
            current_project_data["completed_at"] = datetime.now(timezone.utc).isoformat()
        elif "status" in updates and updates["status"] != "completed" and "completed_at" in current_project_data:
            current_project_data["completed_at"] = None

        try:
            logger.debug(f"CRUD | Project | _update_project_data: Calling save_project_data for project '{project_id}'.") # Novo log
            await save_project_data(user_id, project_id, current_project_data)
            logger.info(f"CRUD | Project | Project '{project_id}' updated for user '{user_id}'. Changes: {list(updates.keys())}. Data updated successfully to Firestore.")
            projects_data = await get_all_projects(user_id) # Buscar projetos atualizados
            return {"status": "success", "message": "Projeto atualizado com sucesso.", "html_view_data": {"projetos": projects_data}} # Retornar dict
        except Exception as e:
            logger.error(f"CRUD | Project | Failed to update project in Firestore for user '{user_id}': {e}", exc_info=True)
            return {"status": "error", "message": "Não foi possível atualizar o projeto."} # Retornar dict

    logger.warning(f"CRUD | Project | Update failed: Project ID '{project_id}' not found for user '{user_id}'.")
    return {"status": "error", "message": "Não foi possível encontrar o projeto para atualização."} # Retornar dict

async def _delete_project_fully(user_id: str, project_id: str) -> Dict[str, Any]: # Modificado para retornar dict
    logger.debug(f"CRUD | Project | _delete_project_fully: Entered for user '{user_id}', project_id '{project_id}'.") # Novo log
    project_doc_ref = get_project_doc_ref(user_id, project_id)

    if await get_project_data(user_id, project_id):
        try:
            logger.debug(f"CRUD | Project | Attempting to delete project doc at: {project_doc_ref.path} for user '{user_id}'.")
            await asyncio.to_thread(project_doc_ref.delete)
            logger.info(f"CRUD | Project | Project '{project_id}' deleted for user '{user_id}'.")
            projects_data = await get_all_projects(user_id) # Buscar projetos atualizados
            return {"status": "success", "message": "Projeto excluído com sucesso.", "html_view_data": {"projetos": projects_data}} # Retornar dict
        except Exception as e:
            logger.error(f"CRUD | Project | Failed to delete project document '{project_id}' for user '{user_id}': {e}", exc_info=True)
            return {"status": "error", "message": "Não foi possível excluir o projeto."} # Retornar dict
    else:
        logger.warning(f"CRUD | Project | Delete failed: Project ID '{project_id}' not found for user '{user_id}'.")
        return {"status": "error", "message": "Não foi possível encontrar o projeto para exclusão."} # Retornar dict

# --- Orquestrador Principal de Ações CRUD (Chamado pelo Frontend) ---
async def orchestrate_crud_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Processa ações CRUD vindas do frontend.
    Valida o payload e roteia a ação para as funções CRUD internas apropriadas.
    Payload esperado:
    {
      "user_id": str,
      "item_type": "task" | "project",
      "action": "create" | "update" | "delete",
      "data": { ...dados específicos... }, # Contém todos os dados da criação/atualização/leitura
      "item_id": str (opcional para 'create', obrigatório para 'update'/'delete')
    }
    """
    logger.debug(f"CRUD_ORCHESTRATOR_ENTERED | Payload received: {payload}") 

    user_id = payload.get('user_id')
    item_type = payload.get('item_type')
    action = payload.get('action')
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
            if not date_str:
                logger.error(f"CRUD | Task | Missing date for action '{action}' for user '{user_id}'. Payload data: {data}")
                return {"status": "error", "message": "A data é obrigatória para operações de tarefa.", "data": {}, "debug_info": debug_info}

            try:
                date.fromisoformat(date_str)
            except (ValueError, TypeError) as e:
                logger.error(f"CRUD | Task | Invalid date format '{date_str}' for user '{user_id}': {e}. Payload data: {data}", exc_info=True)
                return {"status": "error", "message": "Formato de data inválido. Use YYYY-MM-DD.", "data": {}, "debug_info": debug_info}

            if action == 'create':
                description = data.get('description') 
                if not description:
                    logger.error(f"CRUD | Task | Create failed: Description is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "A descrição é obrigatória para criar uma tarefa.", "data": {}, "debug_info": debug_info}
                return await _create_task_data(user_id, date_str, description)

            elif action == 'update':
                if not item_id:
                    logger.error(f"CRUD | Task | Update failed: Task ID is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "O ID da tarefa é obrigatório.", "data": {}, "debug_info": debug_info}
                # _update_task_status_or_data já retorna um dict de sucesso/erro
                return await _update_task_status_or_data(user_id, date_str, item_id, data.get('completed'), data.get('description'))

            elif action == 'delete':
                if not item_id:
                    logger.error(f"CRUD | Task | Delete failed: Task ID is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "O ID da tarefa é obrigatório.", "data": {}, "debug_info": debug_info}
                # _delete_task_by_id já retorna um dict de sucesso/erro
                return await _delete_task_by_id(user_id, date_str, item_id)

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
                # _update_project_data já retorna um dict de sucesso/erro
                return await _update_project_data(user_id, item_id, data) 

            elif action == 'delete':
                if not item_id:
                    logger.error(f"CRUD | Project | Delete failed: Project ID is mandatory for user '{user_id}'. Payload data: {data}")
                    return {"status": "error", "message": "O ID do projeto é obrigatório.", "data": {}, "debug_info": debug_info}
                # _delete_project_fully já retorna um dict de sucesso/erro
                return await _delete_project_fully(user_id, item_id)

        logger.error(f"CRUD | Unknown action or item type: '{item_type}' with action '{action}' for user '{user_id}'. Payload: {payload}")
        return {"status": "error", "message": "Ação ou tipo de item não reconhecido.", "data": {}, "debug_info": debug_info}

    except Exception as e:
        logger.critical(f"CRUD | CRITICAL ERROR: Unexpected error in orchestrate_crud_action for user '{user_id}'. Payload: {payload}: {e}", exc_info=True) 
        # Garante que o debug_info sempre tem user_id, action e item_type
        return {"status": "error", "message": "Ocorreu um erro interno inesperado ao processar a ação CRUD.", "data": {}, "debug_info": {**debug_info, "exception": str(e)}}