# eixa_orchestrator.py
import logging
import asyncio
from datetime import date, datetime, timezone, timedelta
from typing import Dict, Any
import re
import json

# Imports de lógica de negócio e utilitários
from memory_utils import (
    add_emotional_memory,
    get_emotional_memories,
    get_sabotage_patterns,
)
from task_manager import parse_and_update_agenda_items, parse_and_update_project_items 
from eixa_data import get_all_daily_tasks, save_daily_tasks_data, get_project_data, save_project_data, get_user_history, get_all_projects 

from vertex_utils import call_gemini_api 
from vectorstore_utils import get_embedding, add_memory_to_vectorstore, get_relevant_memories 

# Importações de firestore_utils para operar com o Firestore
from firestore_utils import get_user_profile_data, get_firestore_document_data, set_firestore_document, save_interaction
from google.cloud import firestore

from nudger import analyze_for_nudges
from user_behavior import track_repetition 
from personal_checkpoint import get_latest_self_eval
from translation_utils import detect_language, translate_text

import os
import pytz

from config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_TEMPERATURE, DEFAULT_TIMEZONE, USERS_COLLECTION, TOP_LEVEL_COLLECTIONS_MAP, GEMINI_VISION_MODEL, GEMINI_TEXT_MODEL, EMBEDDING_MODEL_NAME 

from input_parser import parse_incoming_input
from app_config_loader import get_eixa_templates 
from crud_orchestrator import orchestrate_crud_action
from profile_settings_manager import parse_and_update_profile_settings, update_profile_from_inferred_data

logger = logging.getLogger(__name__)

async def _extract_crud_intent_with_llm(user_id: str, user_message: str, history: list, gemini_api_key: str, gemini_text_model: str) -> dict | None:
    system_instruction_for_crud_extraction = """
    Você é um assistente de extração de dados altamente preciso. Sua única função é analisar a última mensagem do usuário,
    considerando o histórico de conversas e o perfil do usuário, para identificar INTENÇÕES CLARAS de CRIAÇÃO, ATUALIZAÇÃO ou EXCLUSÃO de TAREFAS OU PROJETOS.

    NÃO converse. SEJA CONCISO. SUA RESPOSTA DEVE SER APENAS UM BLOCO JSON.

    Se uma intenção de tarefa ou projeto for detectada, retorne um JSON com a seguinte estrutura:
    ```json
    {
      "intent_detected": "task" | "project",
      "action": "create" | "update" | "delete" | "complete",
      "item_details": {
        "id": "ID_DO_ITEM_SE_FOR_UPDATE_OU_DELETE",
        "name": "Nome do projeto ou descrição da tarefa",
        "description": "Descrição detalhada (se projeto) ou descrição da tarefa (se tarefa)",
        "date": "YYYY-MM-DD" | null,
        "status": "open" | "completed" | "in_progress" | null
      },
      "confirmation_message": "Você quer que eu adicione 'Comprar pão' para amanhã?"
    }
    ```
    Se a intenção **NÃO FOR CLARA** para um CRUD de tarefa/projeto, ou se a mensagem for ambígua, retorne SOMENTE:
    ```json
    {
      "intent_detected": "none"
    }
    ```
    Considere sinônimos e variações. Ex: "terminar", "finalizar", "concluir" para "complete" (status). "adicione", "crie", "nova" para "create". "mude", "altere" para "update". "remova", "exclua" para "delete".
    Para datas, sempre use o formato ISO (YYYY-MM-DD). "hoje" -> data atual. "amanhã" -> data atual + 1 dia. "próxima segunda" -> a data da próxima segunda-feira. Se nenhuma data for clara, use null.
    Sempre prefira extrair a descrição completa e a data/nome mais preciso.
    """

    llm_history = []
    for turn in history[-5:]: 
        if turn.get("input"):
            llm_history.append({"role": "user", "parts": [{"text": turn.get("input")}]})
        if turn.get("output"):
            llm_history.append({"role": "model", "parts": [{"text": turn.get("output")}]})

    llm_history.append({"role": "user", "parts": [{"text": user_message}]})

    try:
        llm_response_raw = await call_gemini_api(
            api_key=gemini_api_key,
            model_name=gemini_text_model,
            conversation_history=llm_history,
            system_instruction=system_instruction_for_crud_extraction,
            max_output_tokens=1024,
            temperature=0.1
        )

        if llm_response_raw:
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', llm_response_raw, re.DOTALL)
            if json_match:
                extracted_data = json.loads(json_match.group(1))
                logger.debug(f"ORCHESTRATOR | LLM extracted CRUD intent: {extracted_data}")
                return extracted_data
            else:
                logger.warning(f"ORCHESTRATOR | LLM did not return valid JSON for CRUD extraction. Raw response: {llm_response_raw[:200]}...", exc_info=True)
        return {"intent_detected": "none"}
    except Exception as e:
        logger.error(f"ORCHESTRATOR | Error during LLM CRUD intent extraction for user '{user_id}': {e}", exc_info=True)
        return {"intent_detected": "none"}


async def orchestrate_eixa_response(user_id: str, user_message: str = None, uploaded_file_data: Dict[str, Any] = None,
                                     view_request: str = None, gcp_project_id: str = None, region: str = None,
                                     gemini_api_key: str = None, gemini_text_model: str = GEMINI_TEXT_MODEL, 
                                     gemini_vision_model: str = GEMINI_VISION_MODEL, 
                                     firestore_collection_interactions: str = 'interactions',
                                     debug_mode: bool = False) -> Dict[str, Any]:
    
    # Carrega os templates via o novo módulo centralizado
    base_eixa_persona_template_text, user_profile_template_content, user_flags_template_content = get_eixa_templates()

    debug_info_logs = []

    try:
        user_doc_in_eixa_users = await get_firestore_document_data('eixa_user_data', user_id)
        if not user_doc_in_eixa_users:
            logger.info(f"ORCHESTRATOR | Main user document '{user_id}' not found in '{USERS_COLLECTION}'. Creating it.")
            await set_firestore_document(
                'eixa_user_data',
                user_id,
                {"user_id": user_id, "created_at": datetime.now(timezone.utc).isoformat(), "last_active": datetime.now(timezone.utc).isoformat(), "status": "active"}
            )
            logger.info(f"ORCHESTRATOR | Main user document created for '{user_id}' in '{USERS_COLLECTION}'.")
        else:
            logger.debug(f"ORCHESTRATOR | Main user document '{user_id}' already exists in '{USERS_COLLECTION}'.")
    except Exception as e:
        logger.critical(f"ORCHESTRATOR | Failed to ensure main user document in '{USERS_COLLECTION}' for '{user_id}': {e}", exc_info=True)
        return {"status": "error", "response": f"Erro interno ao inicializar dados do usuário: {e}", "debug_info": debug_info_logs}

    # Passa o template de perfil para a função que o carrega/cria
    user_profile = await get_user_profile_data(user_id, user_profile_template_content)
    # NOVO: Fallback mais amigável se o nome não estiver no perfil
    user_display_name = user_profile.get('name') if user_profile.get('name') else f"Novo Usuário EIXA" 
    logger.debug(f"ORCHESTRATOR | User profile loaded for '{user_id}'. Display name: '{user_display_name}'. Profile content keys: {list(user_profile.keys())}")

    eixa_state = user_profile.get('eixa_state', {})
    is_in_confirmation_state = eixa_state.get('awaiting_confirmation', False)
    confirmation_payload_cache = eixa_state.get('confirmation_payload_cache', {})
    stored_confirmation_message = eixa_state.get('confirmation_message', "Aguardando sua confirmação. Por favor, diga 'sim' ou 'não'.")

    # Correct definition and usage of user_flags_data
    user_flags_data_raw = await get_firestore_document_data('flags', user_id)
    user_flags_data = user_flags_data_raw.get("behavior_flags", user_flags_template_content) if user_flags_data_raw else user_flags_template_content

    # Se o documento de flags não existia, salva o template default
    if not user_flags_data_raw:
        await set_firestore_document('flags', user_id, {"behavior_flags": user_flags_data})

    mode_debug_on = debug_mode or user_flags_data.get("debug_mode", False)
    if mode_debug_on:
        debug_info_logs.append("Debug Mode: ON.")

    response_payload = {
        "response": "", "suggested_tasks": [], "suggested_projects": [],
        "html_view_data": {}, "status": "success", "language": "pt", "debug_info": {}
    }

    # Determina qual modelo Gemini final será usado (Vision ou Text)
    gemini_final_model = gemini_vision_model if uploaded_file_data else gemini_text_model 


    await set_firestore_document(
        'eixa_user_data',
        user_id,
        {"last_active": datetime.now(timezone.utc).isoformat()},
        merge=True
    )

    if view_request:
        if view_request == "agenda":
            agenda_data = await get_all_daily_tasks(user_id)
            response_payload["html_view_data"]["agenda"] = agenda_data
            response_payload["response"] = "Aqui estão suas tarefas."
        elif view_request == "projetos":
            projects_data = await get_all_projects(user_id) 
            response_payload["html_view_data"]["projetos"] = projects_data
            response_payload["response"] = "Aqui está a lista dos seus projetos."
        elif view_request == "diagnostico":
            diagnostic_data = await get_latest_self_eval(user_id)
            response_payload["html_view_data"]["diagnostico"] = diagnostic_data
            response_payload["response"] = "Aqui está seu último diagnóstico."
        elif view_request == "emotionalMemories":
            mems_data = await get_emotional_memories(user_id, 10)
            response_payload["html_view_data"]["emotional_memories"] = mems_data
            response_payload["response"] = "Aqui estão suas memórias emocionais recentes."
            logger.info(f"ORCHESTRATOR | Emotional memories requested and provided for user '{user_id}'.")
        elif view_request == "longTermMemory":
            if user_profile.get('eixa_interaction_preferences', {}).get('display_profile_in_long_term_memory', False):
                response_payload["html_view_data"]["long_term_memory"] = user_profile
                response_payload["response"] = "Aqui está seu perfil de memória de longo prazo."
                logger.info(f"ORCHESTRATOR | Long-term memory (profile) requested and provided for user '{user_id}'.")
            else:
                response_payload["status"] = "info"
                response_payload["response"] = "A exibição do seu perfil completo na memória de longo prazo está desativada. Se desejar ativá-la, por favor me diga 'mostrar meu perfil'."
                logger.info(f"ORCHESTRATOR | Long-term memory (profile) requested but display is disabled for user '{user_id}'.")
        else:
            response_payload["status"] = "error"
            response_payload["response"] = "View solicitada inválida."

        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"] = debug_info_logs
        return response_payload

    if not user_message and not uploaded_file_data:
        return {"status": "error", "response": "Nenhuma mensagem ou arquivo fornecido para interação."}

    user_input_for_saving = user_message or (uploaded_file_data.get('filename') if uploaded_file_data else "Ação do sistema")

    # Adicionando debug para o idioma antes da chamada da API
    logger.debug(f"ORCHESTRATOR | Raw user message for language detection: '{user_message}'")
    source_language = await detect_language(user_message or "Olá")
    response_payload["language"] = source_language
    logger.info(f"ORCHESTRATOR | Detected source language: '{source_language}' for user '{user_id}'.")

    user_message_for_processing = user_message
    if source_language != 'pt' and user_message:
        logger.info(f"ORCHESTRATOR | Translating user message from '{source_language}' to 'pt'. Original: '{user_message[:50]}...'.")
        translated_ai_response = await translate_text(user_message, "pt", source_language)
        if translated_ai_response is None:
            logger.error(f"ORCHESTRATOR | Failed to translate user message from '{source_language}' to 'pt' for user '{user_id}'. Original: '{user_message}'.", exc_info=True)
            return {"status": "error", "response": f"Ocorreu um problema ao traduzir sua mensagem de {source_language}.", "debug_info": debug_info_logs}
        user_message_for_processing = translated_ai_response
        logger.info(f"ORCHESTRATOR | User message after translation: '{user_message_for_processing[:50]}...'.")

    # Fetch full history before potential early exit due to confirmation logic
    full_history = await get_user_history(user_id, firestore_collection_interactions, limit=20)

    # --- Handle confirmation state FIRST ---
    if is_in_confirmation_state and confirmation_payload_cache:
        lower_message = user_message_for_processing.lower().strip()
        
        # NOVO DEBUG: Logar a mensagem em minúsculas para entender a falha de correspondência
        logger.debug(f"ORCHESTRATOR | Inside confirmation block. lower_message='{lower_message}'.")

        # LISTA DE KEYWORDS MAIS EXAUSTIVA E ROBUSTA PARA CONFIRMAÇÃO
        confirmation_keywords = [
            "sim", "ok", "confirmo", "confirma", "adicione", "crie", "pode",
            "certo", "beleza", "isso", "deletar", "excluir", "remover",
            "concluir", "finalizar", "ok, faça",
            "sim, por favor", "sim por favor", "claro", "definitivamente", # Adicionadas frases exatas e sinônimos
            "vai", "fazer", "execute", "prossiga", "adiante" # Mais sinônimos
        ]
        
        if any(keyword in lower_message for keyword in confirmation_keywords):
            logger.debug(f"ORCHESTRATOR | Positive confirmation keyword detected: '{lower_message}'.")
            payload_to_execute = confirmation_payload_cache
            item_type = payload_to_execute.get('item_type')
            action = payload_to_execute.get('action')
            item_id = payload_to_execute.get('item_id')
            data = payload_to_execute.get('data', {})

            logger.debug(f"ORCHESTRATOR | Calling orchestrate_crud_action with payload from confirmation cache: {{'user_id': '{user_id}', 'item_type': '{item_type}', 'action': '{action}', 'item_id': '{item_id}', 'data': {data}}}")

            crud_response = await orchestrate_crud_action(
                {"user_id": user_id, "item_type": item_type, "action": action, "item_id": item_id, "data": data}
            )

            # Aprimoramento da mensagem final APÓS A CONFIRMAÇÃO DO CRUD
            if crud_response.get("status") == "success":
                final_ai_response = crud_response.get("message", "Ação concluída com sucesso.")
                if action == "create":
                    final_ai_response += " Como posso te ajudar a dar os primeiros passos?"
                elif action == "update" and data.get("completed"):
                    final_ai_response += " Que ótimo! Qual o próximo passo ou tarefa em que você quer focar?"
                elif action == "delete":
                    final_ai_response += " O que mais podemos otimizar ou organizar?"
                else:
                    final_ai_response += " O que mais posso fazer por você?"
                response_payload["status"] = "success"
                logger.info(f"ORCHESTRATOR | User '{user_id}' confirmed action. CRUD executed successfully.")
            elif crud_response.get("status") == "duplicate":
                final_ai_response = crud_response.get("message", "Ação não realizada: item duplicado.")
                response_payload["status"] = "warning" 
                logger.info(f"ORCHESTRATOR | User '{user_id}' confirmed action, but detected as duplicate.")
            else: 
                final_ai_response = crud_response.get("message", "Houve um erro ao executar a ação confirmada.")
                response_payload["status"] = "error"
                logger.error(f"ORCHESTRATOR | User '{user_id}' confirmed action, but CRUD failed: {crud_response}")

            try:
                await set_firestore_document(
                    'profiles',
                    user_id,
                    {'user_profile.eixa_state': {}}, 
                    merge=True 
                )
                logger.info(f"ORCHESTRATOR | Confirmation state cleared for user '{user_id}'.")
            except Exception as e:
                logger.error(f"ORCHESTRATOR | Failed to clear confirmation state for user '{user_id}': {e}", exc_info=True)

            response_payload["response"] = final_ai_response
            response_payload["debug_info"] = {
                "intent_detected": item_type, 
                "action_confirmed": action,
                "item_type_confirmed": item_type,
                "crud_result_status": crud_response.get("status"), 
                "tarefas_ativas_injetadas": 0, 
                "memoria_emocional_tags": [], 
                "padroes_sabotagem_detectados": {}, 
            }
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return response_payload 

        elif any(keyword in lower_message for keyword in ["não", "nao", "cancela", "esquece", "pare", "não quero", "nao quero", "negativo", "desisto"]): # Adicionado mais opções para cancelamento
            logger.debug(f"ORCHESTRATOR | Negative confirmation keyword detected: '{lower_message}'.")
            final_ai_response = "Ok, entendi. Ação cancelada."
            response_payload["status"] = "success" 
            try:
                await set_firestore_document(
                    'profiles',
                    user_id,
                    {'user_profile.eixa_state': {}}, 
                    merge=True
                )
                logger.info(f"ORCHESTRATOR | Confirmation state cleared for user '{user_id}'.")
            except Exception as e:
                logger.error(f"ORCHESTRATOR | Failed to clear confirmation state (rejection) for user '{user_id}': {e}", exc_info=True)

            response_payload["response"] = final_ai_response + " Como posso ajudar de outra forma?"
            response_payload["debug_info"] = {
                "intent_detected": "cancellation",
                "action_confirmed": "cancel",
                "item_type_confirmed": "none",
                "crud_result_status": "cancelled", 
            }
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return response_payload 
        else:
            # NOVO DEBUG: A mensagem é ambígua, re-promovendo.
            logger.debug(f"ORCHESTRATOR | User response '{lower_message}' in confirmation state was ambiguous. Re-prompting.")
            response_payload["response"] = stored_confirmation_message 
            response_payload["status"] = "awaiting_confirmation"
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return response_payload 

    # --- Normal LLM conversation flow (if no CRUD intent detected) ---
    # NOVO DEBUG: Entrando no fluxo de LLM normal
    logger.debug(f"ORCHESTRATOR | Not in confirmation state or confirmation handled. Proceeding with LLM inference.")

    input_parser_results = await asyncio.to_thread(parse_incoming_input, user_message_for_processing, uploaded_file_data)
    user_prompt_parts = input_parser_results['prompt_parts_for_gemini']
    gemini_model_override = input_parser_results['gemini_model_override']
    logger.info(f"ORCHESTRATOR | Input parser results for user '{user_id}' - Model override: {gemini_model_override}, Prompt parts count: {len(user_prompt_parts)}.")

    profile_settings_results = await parse_and_update_profile_settings(user_id, user_message_for_processing, user_profile_template_content)
    if profile_settings_results.get("profile_updated"):
        direct_action_message = profile_settings_results['action_message']
        user_profile = await get_user_profile_data(user_id, user_profile_template_content) 
        intent_detected_in_orchestrator = "configuracao_perfil"
        response_payload["response"] = direct_action_message
        response_payload["status"] = "success"
        response_payload["debug_info"] = {"intent_detected": intent_detected_in_orchestrator, "crud_result_status": "success"} 
        await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
        return response_payload 

    crud_intent_data = await _extract_crud_intent_with_llm(user_id, user_message_for_processing, full_history, gemini_api_key, gemini_text_model)
    intent_detected_in_orchestrator = crud_intent_data.get("intent_detected", "conversa")

    if intent_detected_in_orchestrator in ["task", "project"]:
        item_type = crud_intent_data['intent_detected']
        action = crud_intent_data['action']
        item_details = crud_intent_data['item_details']
        llm_generated_confirmation_message = crud_intent_data['confirmation_message'] 

        if item_type == 'task':
            task_description = item_details.get("name") or item_details.get("description")
            task_date = item_details.get("date") 

            if action == 'create' and (not task_description or not task_date):
                response_payload["response"] = "Não consegui extrair todos os detalhes necessários para a tarefa (descrição e data). Por favor, seja mais específico."
                response_payload["status"] = "error"
                await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                return response_payload

            corrected_task_date = task_date 
            if task_date:
                try:
                    parsed_date_from_llm = datetime.strptime(task_date, "%Y-%m-%d").date()
                    current_utc_date = datetime.now(timezone.utc).date()

                    if parsed_date_from_llm.year < current_utc_date.year:
                        parsed_date_from_llm = parsed_date_from_llm.replace(year=current_utc_date.year)
                        logger.info(f"ORCHESTRATOR | LLM inferred past year. Corrected task date from original {task_date} to current year: {parsed_date_from_llm.isoformat()}.")
                    
                    if parsed_date_from_llm < current_utc_date:
                        parsed_date_from_llm = parsed_date_from_llm.replace(year=current_utc_date.year + 1)
                        logger.info(f"ORCHESTRATOR | Corrected task date was still in the past. Adjusted to next year: {parsed_date_from_llm.isoformat()}.")
                    
                    corrected_task_date = parsed_date_from_llm.isoformat()
                    logger.info(f"ORCHESTRATOR | Final corrected task date (ISO) used for payload: {corrected_task_date}")

                except ValueError as ve:
                    logger.warning(f"ORCHESTRATOR | Task date '{task_date}' from LLM could not be parsed for year correction ({ve}). Using original from LLM as fallback.", exc_info=True)
            completed_status = True if action == 'complete' else (item_details.get('status') == 'completed' if 'status' in item_details else None)
            
            provisional_payload_data = {"description": task_description, "date": corrected_task_date} 
            if completed_status is not None: provisional_payload_data["completed"] = completed_status
            
            provisional_payload = {
                "item_type": "task",
                "action": action if action != 'complete' else 'update',
                "item_id": item_details.get("id"),
                "data": provisional_payload_data
            }

            if action == 'create':
                if corrected_task_date:
                    try:
                        local_tz = pytz.timezone(user_profile.get('timezone', DEFAULT_TIMEZONE))
                        parsed_date_for_display = local_tz.localize(datetime.fromisoformat(corrected_task_date))
                        formatted_date = parsed_date_for_display.strftime("%d de %B de %Y")
                        confirmation_message = f"Confirma que deseja adicionar a tarefa '{task_description}' para {formatted_date}?"
                    except ValueError as ve_display:
                        logger.warning(f"ORCHESTRATOR | Error formatting corrected_task_date '{corrected_task_date}' for display: {ve_display}. Using raw date.", exc_info=True)
                        confirmation_message = f"Confirma que deseja adicionar a tarefa '{task_description}' para a data especificada ({corrected_task_date})?"
                else: 
                     confirmation_message = f"Confirma que deseja adicionar a tarefa '{task_description}'?"
            elif action == 'complete': confirmation_message = f"Confirma que deseja marcar a tarefa '{task_description}' como concluída?"
            elif action == 'update': confirmation_message = f"Confirma que deseja atualizar a tarefa '{task_description}'?"
            elif action == 'delete': confirmation_message = f"Confirma que deseja excluir a tarefa '{task_description}'?"
            else: 
                confirmation_message = llm_generated_confirmation_message or "Confirma esta ação?"

        elif item_type == 'project':
            project_name = item_details.get("name")
            if action == 'create' and not project_name:
                response_payload["response"] = "Não consegui extrair o nome do projeto. Por favor, seja mais específico."
                response_payload["status"] = "error"
                await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                return response_payload

            provisional_payload = {
                "item_type": "project",
                "action": action,
                "item_id": item_details.get("id"),
                "data": item_details
            }
            if not llm_generated_confirmation_message: 
                if action == 'create': confirmation_message = f"Confirma que deseja criar o projeto '{project_name}'?"
                elif action == 'update': confirmation_message = f"Confirma que deseja atualizar o projeto '{project_name}'?"
                elif action == 'delete': confirmation_message = f"Confirma que deseja excluir o projeto '{project_name}'?"
                elif action == 'complete': confirmation_message = f"Confirma que deseja marcar o projeto '{project_name}' como concluído?"
                else: confirmation_message = "Confirma esta ação?"
            else:
                confirmation_message = llm_generated_confirmation_message

        await set_firestore_document(
            'profiles',
            user_id,
            {
                'user_profile.eixa_state': {
                    'awaiting_confirmation': True,
                    'confirmation_payload_cache': provisional_payload,
                    'confirmation_message': confirmation_message 
                }
            },
            merge=True
        )
        logger.info(f"ORCHESTRATOR | LLM inferred {item_type} {action} intent for user '{user_id}'. Awaiting confirmation. Provisional payload: {provisional_payload}")

        response_payload["response"] = confirmation_message
        response_payload["status"] = "awaiting_confirmation"
        response_payload["debug_info"] = {
            "intent_detected": intent_detected_in_orchestrator,
            "action_awaiting_confirmation": action,
            "item_type_awaiting_confirmation": item_type,
            "provisional_payload": provisional_payload,
        }
        await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
        return response_payload

    # --- Normal LLM conversation flow (if no CRUD intent detected) ---
    logger.debug(f"ORCHESTRATOR | Not in confirmation state or confirmation handled. Proceeding with LLM inference.")
    conversation_history = []
    for turn in full_history:
        if turn.get("input"):
            conversation_history.append({"role": "user", "parts": [{"text": turn.get("input")}]})
        if turn.get("output"):
            conversation_history.append({"role": "model", "parts": [{"text": turn.get("output")}]})

    debug_info_logs.append(f"History prepared with {len(full_history)} turns for LLM context.")
    logger.debug(f"ORCHESTRATOR | Conversation history sent to Gemini for user '{user_id}': {conversation_history[:2]}...")

    # --- INJETAR CONTEXTO TEMPORAL NO PROMPT DO LLM ---
    current_datetime_utc = datetime.now(timezone.utc)
    current_date_iso = current_datetime_utc.strftime("%Y-%m-%d")
    current_year = current_datetime_utc.year
    day_names_pt = {0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira", 3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo"}
    current_day_of_week = day_names_pt[current_datetime_utc.weekday()]

    contexto_temporal = f"--- CONTEXTO TEMPORAL ATUAL ---\n"
    contexto_temporal += f"A data atual é {current_date_iso} ({current_day_of_week}). O ano atual é {current_year}.\n"
    contexto_temporal += f"Considere esta como a base para inferências de datas relativas (ex: 'amanhã', 'próxima semana', 'quarta-feira').\n"
    contexto_temporal += f"--- FIM DO CONTEXTO TEMPORAL ---\n\n"
    debug_info_logs.append("Temporal context generated for LLM.")

    # --- Memória Vetorial (Contextualização de Longo prazo) ---
    if user_message_for_processing and gcp_project_id and region:
        user_query_embedding = await get_embedding(user_message_for_processing, gcp_project_id, region, model_name=EMBEDDING_MODEL_NAME) 
        if user_query_embedding:
            relevant_memories = await get_relevant_memories(user_id, user_query_embedding, n_results=5) 
            if relevant_memories:
                context_string = "\n".join(["--- CONTEXTO DE MEMÓRIAS RELEVANTES DE LONGO PRAZO:"] + [f"- {mem['content']}" for mem in relevant_memories])
                logger.info(f"ORCHESTRATOR | Adding {len(relevant_memories)} relevant memories to LLM context for user '{user_id}'.")
                conversation_history.insert(0, {"role": "user", "parts": [{"text": context_string}]}) 
            else: 
                logger.debug(f"ORCHESTRATOR | No relevant memories found for user '{user_id}' based on embedding query.")
        else: 
            logger.warning(f"ORCHESTRATOR | Could not generate embedding for user message to retrieve memories for user '{user_id}'. Skipping vector memory retrieval.", exc_info=True)

    conversation_history.append({"role": "user", "parts": user_prompt_parts})

    # --- CONSTRUÇÃO DO CONTEXTO CRÍTICO ---
    contexto_critico = "--- TAREFAS PENDENTES E PROJETOS ATIVOS DO USUÁRIO ---\n"

    current_tasks = await get_all_daily_tasks(user_id)
    flat_current_tasks = [task_data for day_data in current_tasks.values() for task_data in day_data.get('tasks', [])]

    current_projects = await get_all_projects(user_id)

    if flat_current_tasks:
        contexto_critico += "Tarefas Pendentes:\n"
        for task in flat_current_tasks:
            desc = task.get('description', 'Tarefa sem descrição')
            status = task.get('completed', False)
            contexto_critico += f"- {desc} (Status: {'Concluída' if status else 'Pendente'})\n"
    else:
        contexto_critico += "Nenhuma tarefa pendente registrada.\n"

    if current_projects:
        contexto_critico += "Projetos Ativos:\n"
        for proj in current_projects:
            proj_name = proj.get('name', 'N/A')
            proj_status = proj.get('status', 'N/A') 
            contexto_critico += f"- {proj_name} (Status: {proj_status})\n"
    else:
        contexto_critico += "Nenhum projeto ativo registrado.\n"
    contexto_critico += "--- FIM DO CONTEXTO CRÍTICO ---\n\n"
    debug_info_logs.append("Critical context generated for LLM.")

    # --- Construção do system_instruction dinâmico para o LLM (APRIMORADO) ---
    base_persona_with_name = base_eixa_persona_template_text.replace("{{user_display_name}}", user_display_name)

    contexto_perfil_str = f"--- CONTEXTO DO PERFIL DO USUÁRIO ({user_display_name}):\n"
    profile_summary_parts = []

    # Ajustado para o perfil minimalista: verifica se as chaves existem e se as listas não estão vazias
    if user_profile.get('psychological_profile'):
        psych = user_profile['psychological_profile']
        if psych.get('personality_traits') and isinstance(psych['personality_traits'], list) and psych['personality_traits']: profile_summary_parts.append(f"  - Traços de Personalidade: {', '.join(psych['personality_traits'])}")
        if psych.get('diagnoses_and_conditions') and isinstance(psych['diagnoses_and_conditions'], list) and psych['diagnoses_and_conditions']: profile_summary_parts.append(f"  - Condições/Diagnósticos: {', '.join(psych['diagnoses_and_conditions'])}")
        if psych.get('historical_behavioral_patterns') and isinstance(psych['historical_behavioral_patterns'], list) and psych['historical_behavioral_patterns']: profile_summary_parts.append(f"  - Padrões Comportamentais Históricos: {', '.join(psych['historical_behavioral_patterns'])}")
        if psych.get('coping_mechanisms') and isinstance(psych['coping_mechanisms'], list) and psych['coping_mechanisms']: profile_summary_parts.append(f"  - Mecanismos de Coping: {', '.join(psych['coping_mechanisms'])}")

    if user_profile.get('cognitive_style') and isinstance(user_profile['cognitive_style'], list) and user_profile['cognitive_style']:
        profile_summary_parts.append(f"  - Estilo Cognitivo: {', '.join(user_profile['cognitive_style'])}")

    if user_profile.get('communication_preferences'):
        comm_pref = user_profile['communication_preferences']
        if comm_pref.get('tone_preference'): profile_summary_parts.append(f"  - Preferências de Comunicação (Tom): {comm_pref['tone_preference']}")
        if comm_pref.get('intervention_style'): profile_summary_parts.append(f"  - Preferências de Comunicação (Estilo de Intervenção): {comm_pref['intervention_style']}")
        if comm_pref.get('specific_no_gos') and isinstance(comm_pref['specific_no_gos'], list) and comm_pref['specific_no_gos']: profile_summary_parts.append(f"  - Regras Específicas para EIXA (NÃO FAZER): {'; '.join(comm_pref['specific_no_gos'])}")

    if user_profile.get('current_projects') and isinstance(user_profile['current_projects'], list) and user_profile['current_projects']:
        project_names = [p.get('name', 'N/A') for p in user_profile['current_projects']]
        if project_names: profile_summary_parts.append(f"  - Projetos Atuais: {', '.join(project_names)}")

    if user_profile.get('goals', {}) and isinstance(user_profile['goals'], dict):
        if user_profile['goals'].get('long_term') and isinstance(user_profile['goals']['long_term'], list) and user_profile['goals']['long_term']:
            goals_text = [g.get('value', 'N/A') for g in user_profile['goals']['long_term'] if isinstance(g, dict)]
            if goals_text: profile_summary_parts.append(f"  - Metas de Longo Prazo: {', '.join(goals_text)}")
        if user_profile['goals'].get('medium_term') and isinstance(user_profile['goals']['medium_term'], list) and user_profile['goals']['medium_term']:
            goals_text = [g.get('value', 'N/A') for g in user_profile['goals']['medium_term'] if isinstance(g, dict)]
            if goals_text: profile_summary_parts.append(f"  - Metas de Médio Prazo: {', '.join(goals_text)}")
        if user_profile['goals'].get('short_term') and isinstance(user_profile['goals']['short_term'], list) and user_profile['goals']['short_term']:
            goals_text = [g.get('value', 'N/A') for g in user_profile['goals']['short_term'] if isinstance(g, dict)]
            if goals_text: profile_summary_parts.append(f"  - Metas de Curto Prazo: {', '.join(goals_text)}")

    if user_profile.get('eixa_interaction_preferences', {}).get('expected_eixa_actions') and isinstance(user_profile['eixa_interaction_preferences']['expected_eixa_actions'], list) and user_profile['eixa_interaction_preferences']['expected_eixa_actions']:
        actions_text = user_profile['eixa_interaction_preferences']['expected_eixa_actions']
        profile_summary_parts.append(f"  - Ações Esperadas da EIXA: {', '.join(actions_text)}")
    
    if user_profile.get('daily_routine_elements'):
        daily_routine = user_profile['daily_routine_elements']
        daily_routine_list = []
        if daily_routine.get('sleep_schedule'): daily_routine_list.append(f"Horário de Sono: {daily_routine['sleep_schedule']}")
        if daily_routine.get('exercise_routine'): daily_routine_list.append(f"Rotina de Exercícios: {daily_routine['exercise_routine']}")
        if daily_routine.get('dietary_preferences'): daily_routine_list.append(f"Preferências Alimentares: {daily_routine['dietary_preferences']}")
        if daily_routine.get('hydration_goals'): daily_routine_list.append(f"Metas de Hidratação: {daily_routine['hydration_goals']}")
        if daily_routine.get('supplements') and isinstance(daily_routine['supplements'], list) and daily_routine['supplements']:
            supps = [f"{s.get('name', 'N/A')} ({s.get('purpose', 'N/A')})" for s in daily_routine['supplements'] if isinstance(s, dict)]
            if supps: daily_routine_list.append(f"Suplementos: {', '.join(supps)}")

        if daily_routine.get('alerts_and_reminders'):
            alerts_rem = daily_routine['alerts_and_reminders']
            if alerts_rem.get('hydration'): daily_routine_list.append(f"Alerta Hidratação: {alerts_rem['hydration']}")
            if alerts_rem.get('eye_strain'): daily_routine_list.append(f"Alerta Fadiga Visual: {alerts_rem['eye_strain']}")
            if alerts_rem.get('mobility'): daily_routine_list.append(f"Alerta Mobilidade: {alerts_rem['mobility']}")
            if alerts_rem.get('mindfulness'): daily_routine_list.append(f"Alerta Mindfulness: {alerts_rem['mindfulness']}")
            if alerts_rem.get('meal_times'): daily_routine_list.append(f"Alerta Refeições: {alerts_rem['meal_times']}")
            if alerts_rem.get('medication_reminders') and isinstance(alerts_rem['medication_reminders'], list) and alerts_rem['medication_reminders']: daily_routine_list.append(f"Alerta Medicação: {', '.join(alerts_rem['medication_reminders'])}")
            if alerts_rem.get('overwhelm_triggers') and isinstance(alerts_rem['overwhelm_triggers'], list) and alerts_rem['overwhelm_triggers']: daily_routine_list.append(f"Gatilhos Sobrecarga: {', '.join(alerts_rem['overwhelm_triggers'])}")
            if alerts_rem.get('burnout_indicators') and isinstance(alerts_rem['burnout_indicators'], list) and alerts_rem['burnout_indicators']: daily_routine_list.append(f"Indicadores Burnout: {', '.join(alerts_rem['burnout_indicators'])}")
        
        if daily_routine_list: profile_summary_parts.append(f"  - Elementos da Rotina Diária: {'; '.join(daily_routine_list)}")


    contexto_perfil_str += "\n".join(profile_summary_parts) if profile_summary_parts else "  Nenhum dado de perfil detalhado disponível.\n"
    contexto_perfil_str += "--- FIM DO CONTEXTO DE PERFIL ---\n\n"

    # Concatena todos os contextos para a system_instruction final
    final_system_instruction = contexto_temporal + contexto_critico + contexto_perfil_str + base_persona_with_name

    logger.info(f"ORCHESTRATOR | Calling Gemini API with model '{gemini_final_model}' for user '{user_id}'.")
    gemini_response_text_in_pt = await call_gemini_api(
        api_key=gemini_api_key,
        model_name=gemini_final_model,
        conversation_history=conversation_history,
        system_instruction=final_system_instruction,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        temperature=DEFAULT_TEMPERATURE
    )
    logger.info(f"ORCHESTRATOR | Gemini API call completed for user '{user_id}'. Raw response received: {gemini_response_text_in_pt is not None}.")

    final_ai_response = gemini_response_text_in_pt

    if not final_ai_response:
        final_ai_response = "Não consegui processar sua solicitação no momento. Tente novamente."
        response_payload["status"] = "error"
        logger.error(f"ORCHESTRATOR | Gemini response was None or empty for user '{user_id}'. Setting response_payload status to 'error'.", exc_info=True)
    else:
        profile_update_json = None 
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', final_ai_response, re.DOTALL)
        if json_match:
            try:
                profile_update_json_str = json_match.group(1)
                profile_update_data = json.loads(profile_update_json_str)
                profile_update_json = profile_update_data.get('profile_update')

                # CORREÇÃO ROBUSTA para remover o JSON da resposta
                # Extrai o texto antes e depois do bloco JSON COM os backticks
                pre_json_text = final_ai_response[:json_match.start()].strip()
                post_json_text = final_ai_response[json_match.end():].strip()
                
                # Reconstrói a resposta sem o bloco JSON
                final_ai_response = (pre_json_text + "\n\n" + post_json_text).strip()
                final_ai_response = final_ai_response.replace('\n\n\n', '\n\n') # Limpa quebras de linha triplas
                
                logger.info(f"ORCHESTRATOR | Detected profile_update JSON from LLM for user '{user_id}'.")
            except json.JSONDecodeError as e:
                logger.warning(f"ORCHESTRATOR | Failed to parse profile_update JSON from LLM: {e}. Raw JSON: {json_match.group(1)[:100]}...", exc_info=True)
            except AttributeError as e:
                logger.warning(f"ORCHESTRATOR | Profile update JSON missing 'profile_update' key: {e}. Raw data: {profile_update_data}", exc_info=True)


        if source_language != "pt":
            logger.info(f"ORCHESTRATOR | Translating AI response from 'pt' to '{source_language}' for user '{user_id}'. Original: '{final_ai_response[:50]}...'.")
            translated_ai_response = await translate_text(final_ai_response, source_language, "pt")

            if translated_ai_response is None:
                logger.error(f"ORCHESTRATOR | Translation of AI response failed for user '{user_id}'. Original PT: '{final_ai_response}'. Target language: '{source_language}'.", exc_info=True)
                fallback_error_msg_pt = "Ocorreu um problema ao traduzir minha resposta. Por favor, tente novamente."
                translated_fallback = await translate_text(fallback_error_msg_pt, source_language, "pt")
                final_ai_response = translated_fallback if translated_fallback is not None else fallback_error_msg_pt
                response_payload["status"] = "error"
            else:
                final_ai_response = translated_ai_response
            logger.info(f"ORCHESTRATOR | AI response after translation: '{final_ai_response[:50]}...'.")
        else:
            logger.info(f"ORCHESTRATOR | No translation needed for AI response (source_language is 'pt') for user '{user_id}'.")

    response_payload["response"] = final_ai_response
    if response_payload["status"] == "success" and ("Não consegui processar sua solicitação" in final_ai_response or "Ocorreu um problema ao traduzir" in final_ai_response):
        response_payload["status"] = "error"
        logger.warning(f"ORCHESTRATOR | Response for user '{user_id}' contained a fallback error message, forcing status to 'error'.")


    await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
    logger.info(f"ORCHESTRATOR | Interaction saved for user '{user_id}'. Final response status: {response_payload['status']}.")

    if profile_update_json:
        await update_profile_from_inferred_data(user_id, profile_update_json, user_profile_template_content)
        logger.info(f"ORCHESTRATOR | Profile updated based on LLM inference for user '{user_id}'.")


    if user_message_for_processing and final_ai_response and gcp_project_id and region:
        text_for_embedding = f"User: {user_message_for_processing}\nAI: {final_ai_response}"
        interaction_embedding = await get_embedding(text_for_embedding, gcp_project_id, region, model_name=EMBEDDING_MODEL_NAME) 
        if interaction_embedding:
            current_utc_timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "_")
            await add_memory_to_vectorstore(
                user_id=user_id,
                input_text=user_message_for_processing,
                output_text=final_ai_response,
                language=source_language,
                timestamp_for_doc_id=current_utc_timestamp,
                embedding=interaction_embedding
            )
            logger.info(f"ORCHESTRATOR | Interaction embedding for user '{user_id}' submitted for asynchronous saving.")
        else:
            logger.warning(f"ORCHESTRATOR | Could not generate embedding for interaction for user '{user_id}'. Skipping vector memory saving.", exc_info=True)

    emotional_tags = []
    lower_input = user_input_for_saving.lower()

    if intent_detected_in_orchestrator == "tarefa":
        emotional_tags.append("tarefa_criada")
    if intent_detected_in_orchestrator == "projeto":
        emotional_tags.append("projeto_criado")

    # Adicionado logs para depurar a detecção de padrões de sabotagem
    sabotage_patterns_detected = await get_sabotage_patterns(user_id, 20, user_profile)
    logger.debug(f"ORCHESTRATOR | Raw sabotage patterns detected: {sabotage_patterns_detected}")

    # Certifica-se de que a detecção de tags emocionais use o lower_input
    if any(w in lower_input for w in ["frustrad", "cansad", "difícil", "procrastin", "adiando", "não consigo", "sobrecarregado"]):
        emotional_tags.append("frustração")
    if any(w in lower_input for w in ["animado", "feliz", "produtivo", "consegui"]):
        emotional_tags.append("positividade")

    psych_profile = user_profile.get('psychological_profile', {})
    if psych_profile.get('diagnoses_and_conditions'):
        for cond in psych_profile['diagnoses_and_conditions']:
            cond_phrase = cond.lower().replace("_", " ")
            if cond_phrase in lower_input or cond_phrase in final_ai_response.lower():
                emotional_tags.append(cond.replace(" ", "_"))

    if psych_profile.get('historical_behavioral_patterns'):
        for pattern in psych_profile['historical_behavioral_patterns']:
            pattern_phrase = pattern.lower().replace("_", " ")
            if pattern_phrase in lower_input or pattern_phrase in final_ai_response.lower():
                emotional_tags.append(pattern.replace(" ", "_"))

    if psych_profile.get('coping_mechanisms'):
        for coping_mechanism in psych_profile['coping_mechanisms']:
            coping_phrase = coping_mechanism.lower().replace("_", " ")
            if coping_phrase in lower_input or coping_phrase in final_ai_response.lower():
                emotional_tags.append(coping_mechanism.replace(" ", "_"))

    if emotional_tags:
        await add_emotional_memory(user_id, user_input_for_saving + " | " + final_ai_response, list(set(emotional_tags)))
        logger.info(f"ORCHESTRATOR | Emotional memory for user '{user_id}' with tags {list(set(emotional_tags))} submitted for asynchronous saving.")

    nudge_message = await analyze_for_nudges(
        user_id, user_message_for_processing, full_history, user_flags_data, 
        user_profile=user_profile
    )
    if nudge_message:
        response_payload["response"] = nudge_message + "\n\n" + response_payload["response"]
        logger.info(f"ORCHESTRATOR | Generated nudge for user '{user_id}': '{nudge_message[:50]}...'.")

    # Re-executar a detecção de padrões de sabotagem após a mensagem principal, se não foi feita antes
    # ou para garantir que a resposta foi influenciada por ela.
    # A detecção é chamada em get_sabotage_patterns.
    # Certifique-se que get_sabotage_patterns é robusto e não depende de histórico *imediatamente* após a interação atual,
    # mas sim do histórico persistido.

    # A detecção de sabotagem foi chamada antes para o LLM. A exibição deve ser feita no final.
    filtered_patterns = {p: f for p, f in sabotage_patterns_detected.items() if f >= 2} # Usa a variável de cima
    if filtered_patterns:
        response_payload["response"] += "\n\n⚠️ **Padrões de auto-sabotagem detectados:**\n" + "\n".join(f"- \"{p}\" ({str(f)} vezes)" for p, f in filtered_patterns.items())
        logger.info(f"ORCHESTRATOR | Detected and added {len(filtered_patterns)} sabotage patterns to response for user '{user_id}'.")

    if mode_debug_on:
        if "orchestrator_debug_log" not in response_payload["debug_info"]:
            response_payload["debug_info"]["orchestrator_debug_log"] = []
        response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        response_payload["debug_info"]["user_profile_loaded"] = 'true' if user_profile else 'false'
        response_payload["debug_info"]["user_flags_loaded"] = 'true' if user_flags_data else 'false'
        response_payload["debug_info"]["generated_nudge"] = 'true' if nudge_message else 'false'
        response_payload["debug_info"]["system_instruction_snippet"] = final_system_instruction[:500] + "..."


    return response_payload