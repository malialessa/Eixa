import logging
import re
import asyncio
import dateparser
from datetime import datetime, date, timedelta, timezone
import pytz

from firestore_utils import set_firestore_document, get_firestore_document_data
from eixa_data import get_all_projects, get_all_daily_tasks, get_daily_tasks_data

logger = logging.getLogger(__name__)

def task_date_str_formatted(iso_date_str: str) -> str:
    """Formata uma string ISO de data para DD/MM/YYYY."""
    if not iso_date_str:
        return "N/A"
    try:
        dt_obj = datetime.fromisoformat(iso_date_str).date()
        return dt_obj.strftime('%d/%m/%Y')
    except (ValueError, TypeError) as e:
        logger.warning(f"Invalid date format for ISO string '{iso_date_str}': {e}. Returning original string.")
        return iso_date_str

def _parse_date_from_text_sync(text: str, current_datetime_base: datetime) -> str | None:
    """
    Tenta parsear uma data de uma string de texto, usando uma data/hora base.
    Retorna a data no formato 'YYYY-MM-DD' ou None.
    """
    try:
        parsed_dt = dateparser.parse(
            text,
            languages=['pt', 'en'],
            settings={
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': current_datetime_base,
                'TIMEZONE': current_datetime_base.tzinfo.tzname(current_datetime_base) if current_datetime_base.tzinfo else None,
                'RETURN_AS_TIMEZONE_AWARE': True
            }
        )
        if parsed_dt:
            return parsed_dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        return None
    except Exception as e:
        logger.error(f"Error parsing date from text '{text}': {e}", exc_info=True)
        return None

# NOTE: These functions (parse_and_update_agenda_items, parse_and_update_project_items)
# are now primarily retained for their internal logic or as a fallback/validation
# if the LLM-based intent extraction (in eixa_orchestrator) is not sufficient.
# The primary flow for CRUD from chat will now go through LLM extraction and confirmation.
# The `orchestrate_eixa_response` will *not* call these directly for chat-based CRUD anymore.

async def parse_and_update_agenda_items(user_id: str, message: str, current_date: str) -> dict:
    if not message:
        return {"action_message": "", "suggested_tasks": [], "crud_payloads": []}

    lower_message = message.lower()
    action_message = ""
    crud_payloads = []

    try:
        user_timezone = pytz.timezone('America/Sao_Paulo') # Idealmente do user_profile
        current_datetime_local = user_timezone.localize(datetime.strptime(current_date, "%Y-%m-%d"))
        logger.debug(f"TASK_MANAGER: current_datetime_local for parsing: {current_datetime_local}")
    except Exception as e:
        logger.error(f"Failed to create current_datetime_local from '{current_date}': {e}. Using naive datetime.now().")
        current_datetime_local = datetime.now()


    # --- Lógica para ADICIONAR TAREFA (REFINADA E MAIS ROBUSTA) ---
    # Nova Regex mais flexível para capturar a descrição e, opcionalmente, uma data/prazo.
    # Ex: "adicione uma tarefa para amanhã" -> desc="uma tarefa", data="amanhã"
    # Ex: "adicione tarefa de comprar pão sexta-feira" -> desc="de comprar pão", data="sexta-feira"
    # Ex: "nova tarefa: enviar email" -> desc="enviar email", data="" (vazio)
    add_task_match = re.search(
        r'(?:adicione(?: uma)?|crie|nova)\s+tarefa[:\s]*(.+?)(?:\s+(?:para|em|no dia|dia|at[ée]|antes de)\s+(.*?))?(?:\s+|$)',
        lower_message, re.IGNORECASE
    )

    if add_task_match:
        task_description_raw = add_task_match.group(1).strip()
        # group(2) pode ser None se a parte da data não casar
        date_part_from_message = add_task_match.group(2).strip() if add_task_match.group(2) else ""
        
        logger.debug(f"TASK_MANAGER: Detected 'add task'. Raw Desc: '{task_description_raw}', Raw Date Part: '{date_part_from_message}'")

        task_description_final = task_description_raw
        task_date_final_str = None

        if date_part_from_message:
            parsed_date_dt = await asyncio.to_thread(_parse_date_from_text_sync, date_part_from_message, current_datetime_local)
            if parsed_date_dt:
                task_date_final_str = parsed_date_dt
                logger.debug(f"TASK_MANAGER: Date successfully parsed as: {task_date_final_str}")
            else:
                logger.warning(f"TASK_MANAGER: Failed to parse date from '{date_part_from_message}'. Will default to current date.")
        
        # Se nenhuma data foi explicitamente parseada ou extraída, a data final é current_date
        if not task_date_final_str:
            task_date_final_str = current_date
            logger.debug(f"TASK_MANAGER: No explicit date parsed. Defaulting to current_date: {task_date_final_str}")

        # VALIDAÇÃO CRÍTICA: Descrição da tarefa não pode ser vazia ou muito vaga
        # Considera descrições válidas com pelo menos 3 caracteres alfabéticos ou números
        if not task_description_final or re.fullmatch(r'[\s\W\d]*', task_description_final): # Se for só espaço, pontuação ou números
            action_message = "Não consegui identificar uma descrição clara para a tarefa. Por favor, seja mais específico."
            logger.warning(f"TASK_MANAGER: Task description is too vague or empty: '{task_description_final}'.")
            return {"action_message": action_message, "suggested_tasks": [], "crud_payloads": []}

        # Check for duplication before preparing payload
        daily_data = await get_daily_tasks_data(user_id, task_date_final_str)
        tasks_for_day = daily_data.get("tasks", [])

        is_duplicate = any(
            t.get("description", "").lower() == task_description_final.lower() and not t.get("completed", False)
            for t in tasks_for_day
        )

        if is_duplicate:
            action_message = f"A tarefa '{task_description_final}' para {task_date_str_formatted(task_date_final_str)} já existe e não foi adicionada novamente."
            logger.warning(f"TASK_MANAGER: Duplicate task detected for '{task_description_final}' on '{task_date_final_str}'.")
        else:
            crud_payloads.append({
                "item_type": "task",
                "action": "create",
                "data": {"description": task_description_final, "date": task_date_final_str}
            })
            action_message = f"Ação de criar tarefa '{task_description_final}' para {task_date_str_formatted(task_date_final_str)} foi preparada."
            logger.info(f"TASK_MANAGER: Prepared CRUD payload for task creation: '{task_description_final}' on '{task_date_final_str}'")
        
        return {"action_message": action_message, "suggested_tasks": [], "crud_payloads": crud_payloads}

    # --- Lógica para ATUALIZAR/FINALIZAR TAREFA ---
    update_task_keywords = ["finalizar ", "concluir ", "completei ", "terminei ", "atualizar tarefa ", "mudar tarefa ", "alterar tarefa "]
    for keyword in update_task_keywords:
        match = re.search(f"{keyword}(.+)", lower_message, re.IGNORECASE)
        if match:
            update_desc_part = match.group(1).strip()
            update_status = True if "finalizar" in keyword or "concluir" in keyword or "terminei" in keyword or "completei" in keyword else False
            logger.debug(f"TASK_MANAGER: Detected update/complete task command for: '{update_desc_part}', status: {update_status}")

            found_task_id = None
            found_task_date = None
            all_daily_tasks = await get_all_daily_tasks(user_id)
            
            for date_key, day_data in all_daily_tasks.items():
                for task in day_data.get('tasks', []):
                    if update_desc_part.lower() in task.get('description', '').lower():
                        found_task_id = task['id']
                        found_task_date = date_key
                        break
                if found_task_id:
                    break

            if found_task_id:
                crud_payloads.append({
                    "item_type": "task",
                    "action": "update",
                    "item_id": found_task_id,
                    "data": {"date": found_task_date, "completed": update_status, "description": update_desc_part if not update_status else None}
                })
                action_message = f"Intenção de {('concluir' if update_status else 'atualizar')} tarefa '{update_desc_part}' reconhecida."
                logger.info(f"TASK_MANAGER: Prepared CRUD payload for task update: '{update_desc_part}' (ID: {found_task_id}) on {found_task_date}")
            else:
                action_message = f"Não encontrei a tarefa '{update_desc_part}' para {('concluir' if update_status else 'atualizar')}."
                logger.warning(f"TASK_MANAGER: Task '{update_desc_part}' not found for update/complete.")
            return {"action_message": action_message, "suggested_tasks": [], "crud_payloads": crud_payloads}

    # --- Lógica para REMOVER/DELETAR TAREFA ---
    delete_task_keywords = ["remova a tarefa:", "delete a tarefa:", "exclua a tarefa:"]
    for keyword in delete_task_keywords:
        match = re.search(f"{keyword}(.+)", lower_message, re.IGNORECASE)
        if match:
            delete_desc_part = match.group(1).strip()
            logger.debug(f"TASK_MANAGER: Detected delete task command for: '{delete_desc_part}'")

            found_task_id = None
            found_task_date = None
            all_daily_tasks = await get_all_daily_tasks(user_id)
            for date_key, day_data in all_daily_tasks.items():
                for task in day_data.get('tasks', []):
                    if delete_desc_part.lower() in task.get('description', '').lower():
                        found_task_id = task['id']
                        found_task_date = date_key
                        break
                if found_task_id:
                    break
            
            if found_task_id:
                crud_payloads.append({
                    "item_type": "task",
                    "action": "delete",
                    "item_id": found_task_id,
                    "data": {"date": found_task_date}
                })
                action_message = f"Intenção de deletar tarefa '{delete_desc_part}' reconhecida."
                logger.info(f"TASK_MANAGER: Prepared CRUD payload for task deletion: '{delete_desc_part}' (ID: {found_task_id}) on {found_task_date}")
            else:
                action_message = f"Não encontrei a tarefa '{delete_desc_part}' para remover."
                logger.warning(f"TASK_MANAGER: Task '{delete_desc_part}' not found for deletion.")
            return {"action_message": action_message, "suggested_tasks": [], "crud_payloads": crud_payloads}

    # Se nenhuma intenção de tarefa foi detectada, retorna vazio.
    logger.debug("TASK_MANAGER: Nenhuma intenção de tarefa detectada na mensagem.")
    return {"action_message": "", "suggested_tasks": [], "crud_payloads": []}


async def parse_and_update_project_items(user_id: str, message: str) -> dict:
    """
    Detecta e processa intenções de projeto na mensagem do usuário.
    Retorna uma 'action_message' e, se for uma ação de CRUD, os 'crud_payloads' para o orchestrator.
    """
    if not message:
        return {"action_message": "", "suggested_projects": [], "crud_payloads": []}

    lower_message = message.lower()
    action_message = ""
    suggested_projects = []
    crud_payloads = []

    # Lógica para CRIAR PROJETO
    # Correção: O match_create_project.group(3) agora é checado com segurança
    match_create_project = re.search(r'(?:criar projeto|novo projeto (?:chamado|intitulado|nomeado)?):\s*(.+)', lower_message, re.IGNORECASE)
    if match_create_project:
        project_name = match_create_project.group(1).strip().title() # Alterado para group(1) após ajuste da regex
        logger.debug(f"PROJECT_MANAGER: Detected create project command for: '{project_name}'")

        if not project_name:
            action_message = "Por favor, forneça um nome para o projeto."
            logger.warning("PROJECT_MANAGER: Project name is empty.")
            return {"action_message": action_message, "suggested_projects": [], "crud_payloads": []}

        all_projects = await get_all_projects(user_id)
        if any(p.get("name", "").lower() == project_name.lower() for p in all_projects):
            action_message = f"O projeto '{project_name}' já existe. Deseja adicionar microtarefas a ele ou fazer uma atualização?"
            logger.warning(f"PROJECT_MANAGER: Duplicate project creation attempt for '{project_name}'.")
        else:
            project_data = {
                "name": project_name,
                "description": f"Projeto '{project_name}' iniciado via comando de voz/texto.",
                "status": "open",
                "progress_tags": ["iniciado"],
                "expected_energy_level": "médio",
                "priority": "média",
                "micro_tasks": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "deadline": None
            }
            crud_payloads.append({
                "item_type": "project",
                "action": "create",
                "data": project_data
            })
            action_message = f"Ação de criar projeto '{project_name}' foi preparada."
            logger.info(f"PROJECT_MANAGER: Prepared CRUD payload for project creation: '{project_name}'")

        return {"action_message": action_message, "suggested_projects": suggested_projects, "crud_payloads": crud_payloads}

    # Lógica para ATUALIZAR/FINALIZAR PROJETO (simplificada)
    update_project_keywords = ["finalizar projeto ", "concluir projeto ", "atualizar projeto ", "mudar projeto ", "alterar projeto "]
    for keyword in update_project_keywords:
        match = re.search(f"{keyword}(.+)", lower_message, re.IGNORECASE)
        if match:
            update_name_part = match.group(1).strip()
            new_status = "completed" if "finalizar" in keyword or "concluir" in keyword else None
            
            logger.debug(f"PROJECT_MANAGER: Detected update project command for: '{update_name_part}', new_status: {new_status}")

            found_project_id = None
            all_projects = await get_all_projects(user_id)
            for project in all_projects:
                if update_name_part.lower() in project.get('name', '').lower():
                    found_project_id = project['id']
                    break

            if found_project_id:
                update_data = {}
                if new_status:
                    update_data["status"] = new_status
                
                crud_payloads.append({
                    "item_type": "project",
                    "action": "update",
                    "item_id": found_project_id,
                    "data": update_data
                })
                action_message = f"Intenção de {('concluir' if new_status else 'atualizar')} projeto '{update_name_part}' reconhecida."
                logger.info(f"PROJECT_MANAGER: Prepared CRUD payload for project update: '{update_name_part}' (ID: {found_project_id})")
            else:
                action_message = f"Não encontrei o projeto '{update_name_part}' para {('concluir' if new_status else 'atualizar')}."
                logger.warning(f"PROJECT_MANAGER: Project '{update_name_part}' not found for update/complete.")
            return {"action_message": action_message, "suggested_projects": suggested_projects, "crud_payloads": crud_payloads}

    # Lógica para DELETAR PROJETO
    delete_project_keywords = ["remova o projeto:", "delete o projeto:", "exclua o projeto:"]
    for keyword in delete_project_keywords:
        match = re.search(f"{keyword}(.+)", lower_message, re.IGNORECASE)
        if match:
            delete_name_part = match.group(1).strip()
            logger.debug(f"PROJECT_MANAGER: Detected delete project command for: '{delete_name_part}'")

            found_project_id = None
            all_projects = await get_all_projects(user_id)
            for project in all_projects:
                if delete_name_part.lower() in project.get('name', '').lower():
                    found_project_id = project['id']
                    break
            
            if found_project_id:
                crud_payloads.append({
                    "item_type": "project",
                    "action": "delete",
                    "item_id": found_project_id
                })
                action_message = f"Intenção de deletar projeto '{delete_name_part}' reconhecida."
                logger.info(f"PROJECT_MANAGER: Prepared CRUD payload for project deletion: '{delete_name_part}' (ID: {found_project_id})")
            else:
                action_message = f"Não encontrei o projeto '{delete_name_part}' para remover."
                logger.warning(f"PROJECT_MANAGER: Project '{delete_name_part}' not found for deletion.")
            return {"action_message": action_message, "suggested_projects": suggested_projects, "crud_payloads": crud_payloads}

    logger.debug("PROJECT_MANAGER: Nenhuma intenção de projeto detectada na mensagem.")
    return {"action_message": action_message, "suggested_projects": suggested_projects, "crud_payloads": crud_payloads}