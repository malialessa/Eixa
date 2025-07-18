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
from firestore_utils import ( # Modified: Added confirmation state functions
    get_user_profile_data,
    get_firestore_document_data,
    set_firestore_document,
    save_interaction,
    get_confirmation_state,    # ADDED THIS
    set_confirmation_state,    # ADDED THIS
    clear_confirmation_state   # ADDED THIS
)
from google.cloud import firestore # Importar firestore aqui para usar firestore.DELETE_FIELD

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
    current_date_utc = datetime.now(timezone.utc).date()
    current_date_iso = current_date_utc.isoformat()
    tomorrow_date_iso = (current_date_utc + timedelta(days=1)).isoformat()

    system_instruction_for_crud_extraction = f"""
    A data atual é {current_date_iso}.
    Você é um assistente de extração de dados altamente preciso e sem vieses. Sua única função é analisar **EXCLUSIVAMENTE a última mensagem do usuário**, ignorando todo o contexto anterior, para identificar INTENÇÕES CLARAS e DIRETAS de CRIAÇÃO, ATUALIZAÇÃO, EXCLUSÃO ou MARCAÇÃO DE CONCLUSÃO (COMPLETE) de TAREFAS OU PROJETOS.

    **REGRAS RÍGIDAS DE SAÍDA:**
    1.  **SEMPRE** retorne APENAS um bloco JSON, sem texto conversacional.
    2.  **PRIORIDADE ABSOLUTA:** Se a mensagem do usuário for uma resposta simples de confirmação ou negação (e.g., "Sim", "Não", "Certo", "Ok", "Por favor", "Deletar!", "Adicionar!", "Cancelar", "Concluir!", "Entendido", "Faça", "Prossiga", "Não quero", "Obrigado", "Bom dia", "Não sei por onde começar", "O que é EIXA?"), **VOCÊ DEVE RETORNAR SOMENTE:**
        ```json
        {{
        "intent_detected": "none"
        }}
        ```
        Não tente interpretar essas mensagens como novas intenções de CRUD. Elas são respostas a uma pergunta anterior.
    3.  Se uma intenção de tarefa ou projeto for detectada **CLARAMENTE** na ÚLTIMA MENSAGEM (e não for uma resposta de confirmação/negação), retorne um JSON com a seguinte estrutura:
        ```json
        {{
        "intent_detected": "task" | "project",
        "action": "create" | "update" | "delete" | "complete",
        "item_details": {{
            "id": "ID_DO_ITEM_SE_FOR_UPDATE_OU_DELETE",
            "name": "Nome do projeto ou descrição da tarefa",
            "description": "Descrição detalhada (se projeto) ou descrição da tarefa (se tarefa)",
            "date": "YYYY-MM-DD" | null,
            "status": "open" | "completed" | "in_progress" | null
        }},
        "confirmation_message": "Você quer que eu adicione 'Comprar pão' para amanhã?"
        }}
        ```
        Para datas, sempre use o formato ISO (YYYY-MM-DD). **"hoje" DEVE ser {current_date_iso}. "amanhã" DEVE ser {tomorrow_date_iso}. "próxima segunda" DEVE ser a data da próxima segunda-feira no formato YYYY-MM-DD. Se nenhuma data for clara, use `null`.**
        Sempre prefira extrair a descrição completa e a data/nome mais preciso.

    **EXEMPLOS ADICIONAIS DE COMO RETORNAR "none":**
    - "Qual a sua opinião sobre a vida?" -> `{{ "intent_detected": "none" }}`
    - "Preciso de ajuda com ansiedade." -> `{{ "intent_detected": "none" }}`
    - "O que você acha disso?" -> `{{ "intent_detected": "none" }}`
    """
    logger.debug(f"_extract_crud_intent_with_llm: Processing message '{user_message[:50]}...' for CRUD intent.") # Novo log
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
            max_output_tokens=1024, # Isso é um limite razoável para JSON de extração
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
    
    base_eixa_persona_template_text, user_profile_template_content, user_flags_template_content = get_eixa_templates()

    debug_info_logs = []

    # --- 1. Inicialização e Carregamento de Dados Essenciais ---
    try:
        # Garante que o documento principal do usuário exista
        user_doc_in_eixa_users = await get_firestore_document_data('eixa_user_data', user_id)
        if not user_doc_in_eixa_users:
            logger.info(f"ORCHESTRATOR | Main user document '{user_id}' not found in '{USERS_COLLECTION}'. Creating it.")
            await set_firestore_document(
                'eixa_user_data', user_id,
                {"user_id": user_id, "created_at": datetime.now(timezone.utc).isoformat(), "last_active": datetime.now(timezone.utc).isoformat(), "status": "active"}
            )
        else:
            await set_firestore_document( # Atualiza last_active
                'eixa_user_data', user_id,
                {"last_active": datetime.now(timezone.utc).isoformat()}, merge=True
            )
        user_profile = await get_user_profile_data(user_id, user_profile_template_content)
        user_display_name = user_profile.get('name') if user_profile.get('name') else f"Usuário EIXA"
        
        # MODIFIED: Load confirmation state from dedicated collection
        confirmation_state_data = await get_confirmation_state(user_id) # MODIFIED
        is_in_confirmation_state = confirmation_state_data.get('awaiting_confirmation', False) # MODIFIED
        confirmation_payload_cache = confirmation_state_data.get('confirmation_payload_cache', {}) # MODIFIED
        stored_confirmation_message = confirmation_state_data.get('confirmation_message', "Aguardando sua confirmação. Por favor, diga 'sim' ou 'não'.") # MODIFIED
        
        # LOG MODIFIED to reflect new confirmation state source
        logger.debug(f"ORCHESTRATOR_START | User '{user_id}' req: '{user_message[:50] if user_message else '[no message]'}' | State: is_in_confirmation_state={is_in_confirmation_state}, confirmation_payload_cache_keys={list(confirmation_payload_cache.keys()) if confirmation_payload_cache else 'None'}. Loaded confirmation_state_data={confirmation_state_data}")


        user_flags_data_raw = await get_firestore_document_data('flags', user_id)
        user_flags_data = user_flags_data_raw.get("behavior_flags", user_flags_template_content) if user_flags_data_raw else user_flags_template_content
        if not user_flags_data_raw: # Se não existia, salva o template default
            await set_firestore_document('flags', user_id, {"behavior_flags": user_flags_data})

    except Exception as e:
        logger.critical(f"ORCHESTRATOR | Failed to initialize essential user data for '{user_id}': {e}", exc_info=True)
        response_payload = {"status": "error", "response": f"Erro interno ao inicializar dados do usuário: {e}", "debug_info": {"orchestrator_debug_log": debug_info_logs}}
        return {"response_payload": response_payload}

    mode_debug_on = debug_mode or user_flags_data.get("debug_mode", False)
    if mode_debug_on: debug_info_logs.append("Debug Mode: ON.")

    response_payload = {
        "response": "", "suggested_tasks": [], "suggested_projects": [],
        "html_view_data": {}, "status": "success", "language": "pt", "debug_info": {}
    }

    # --- 2. Processamento de Requisições de Visualização (view_request) ---
    if view_request:
        logger.debug(f"ORCHESTRATOR | Processing view_request: {view_request}") # Novo log
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
        elif view_request == "longTermMemory":
            if user_profile.get('eixa_interaction_preferences', {}).get('display_profile_in_long_term_memory', False):
                response_payload["html_view_data"]["long_term_memory"] = user_profile
                response_payload["response"] = "Aqui está seu perfil de memória de longo prazo."
            else:
                response_payload["status"] = "info" # <-- Mantenha status "info" aqui!
                response_payload["response"] = "A exibição do seu perfil completo na memória de longo prazo está desativada. Se desejar ativá-la, por favor me diga 'mostrar meu perfil'."
                logger.info(f"ORCHESTRATOR | Long-term memory (profile) requested but display is disabled for user '{user_id}'.")
        else:
            response_payload["status"] = "error"
            response_payload["response"] = "View solicitada inválida."

        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        return {"response_payload": response_payload}

    # --- 3. Verificação de Mensagem Vazia ---
    if not user_message and not uploaded_file_data:
        logger.debug("ORCHESTRATOR | No user message or file data provided.") # Novo log
        response_payload["status"] = "error"
        response_payload["response"] = "Nenhuma mensagem ou arquivo fornecido para interação."
        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        return {"response_payload": response_payload}

    # --- 4. Preparação da Mensagem (Idioma, Histórico) ---
    user_input_for_saving = user_message or (uploaded_file_data.get('filename') if uploaded_file_data else "Ação do sistema")
    source_language = await detect_language(user_message or "Olá")
    response_payload["language"] = source_language
    user_message_for_processing = user_message
    logger.debug(f"ORCHESTRATOR | Detected source language: {source_language}") # Novo log
    if source_language != 'pt' and user_message:
        logger.debug(f"ORCHESTRATOR | Translating user message from {source_language} to pt.") # Novo log
        translated_user_message = await translate_text(user_message, "pt", source_language) # Corrigido aqui
        if translated_user_message is None:
            response_payload["status"] = "error"
            response_payload["response"] = f"Ocorreu um problema ao traduzir sua mensagem de {source_language}."
            if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return {"response_payload": response_payload}
        user_message_for_processing = translated_user_message
    
    full_history = await get_user_history(user_id, firestore_collection_interactions, limit=20)
    logger.debug(f"ORCHESTRATOR | Full history retrieved, {len(full_history)} turns. History for LLM: {full_history[-5:]}") # Novo log

    # --- 5. LÓGICA DE CONFIRMAÇÃO PENDENTE (MAIOR PRIORIDADE AQUI!) ---
    # Se o sistema está esperando uma confirmação do usuário.
    # Esta é a LÓGICA DE CONFIRMAÇÃO (Sim/Não) que DEVE ser executada primeiro.
    if is_in_confirmation_state and confirmation_payload_cache:
        logger.debug(f"ORCHESTRATOR | Entered confirmation state logic path.") # Novo log
        lower_message = user_message_for_processing.lower().strip()
        logger.debug(f"ORCHESTRATOR | Confirmation Flow: Message '{lower_message}'. Awaiting state: {is_in_confirmation_state}, Cached payload: {confirmation_payload_cache.get('action')}")

        confirmation_keywords = [
            "sim", "ok", "confirmo", "confirma", "adicione", "crie", "pode",
            "certo", "beleza", "isso", "deletar", "excluir", "remover",
            "concluir", "finalizar", "ok, faça",
            "sim, por favor", "sim por favor", "claro", "definitivamente",
            "vai", "fazer", "execute", "prossiga", "adiante"
        ]
        negative_keywords = ["não", "nao", "cancela", "esquece", "pare", "não quero", "nao quero", "negativo", "desisto"]

        # Verifica se é uma resposta de confirmação POSITIVA
        if any(keyword in lower_message for keyword in confirmation_keywords):
            logger.info(f"ORCHESTRATOR | Confirmation Flow: Positive keyword '{lower_message}' detected. Attempting to execute cached CRUD.")
            payload_to_execute = confirmation_payload_cache
            
            # PONTO DE DEBUGGING CRÍTICO: Log antes de chamar orchestrate_crud_action
            logger.debug(f"ORCHESTRATOR | About to call orchestrate_crud_action with payload: {payload_to_execute}")
            
            crud_response = await orchestrate_crud_action(payload_to_execute) # Passa o payload completo
            
            if crud_response.get("status") == "success":
                logger.debug(f"ORCHESTRATOR | CRUD action returned success: {crud_response.get('message')}") # Novo log
                final_ai_response = crud_response.get("message", "Ação concluída com sucesso.")
                action = payload_to_execute.get('action') # Pega a ação do payload original
                if action == "create": final_ai_response += " Como posso te ajudar a dar os primeiros passos?"
                elif action == "update" and payload_to_execute.get('data', {}).get("completed"): final_ai_response += " Que ótimo! Qual o próximo passo ou tarefa em que você quer focar?"
                elif action == "delete": final_ai_response += " O que mais podemos otimizar ou organizar?"
                else: final_ai_response += " O que mais posso fazer por você?"
                response_payload["status"] = "success"

                # >>> ADIÇÃO CRÍTICA AQUI: INCLUIR DADOS DO HTML_VIEW_DATA DO BACKEND <<<
                # O backend já retorna os dados da view atualizados no `crud_orchestrator`
                # Então, `crud_response` pode conter o `html_view_data` se for uma ação CRUD que afeta uma view
                # Vamos verificar e usar esses dados diretamente
                if crud_response.get('html_view_data'):
                    response_payload["html_view_data"] = crud_response["html_view_data"]
                    logger.debug("ORCHESTRATOR | html_view_data from CRUD response copied to main payload.")
                else:
                    # Fallback: Se o crud_orchestrator não retornou dados de view, recarregue a view
                    # Isso é importante porque o crud_orchestrator em si não necessariamente *sempre*
                    # retorna o html_view_data, ele se concentra na ação CRUD.
                    # Mas no eixa_orchestrator, a chamada à API é 'chat_and_view', que pode esperar.
                    # A lógica do frontend já chama `loadViewData` se `html_view_data` não vier ou se `crud_result_status` for sucesso.
                    # Mantenha essa lógica de recarga no frontend, mas garanta que o backend NÃO esteja tentando buscar aqui
                    # Se o `crud_orchestrator` já busca (e ele busca, via `eixa_data`), ele deveria passar.
                    # Como o `crud_orchestrator` não retorna `html_view_data`, o frontend fará a chamada `loadViewData`.
                    # Não precisamos replicar a lógica `get_all_daily_tasks` aqui.
                    pass
                # >>> FIM DA ADIÇÃO CRÍTICA <<<

                logger.info(f"ORCHESTRATOR | User '{user_id}' confirmed action. CRUD executed successfully.")
            elif crud_response.get("status") == "duplicate":
                logger.warning(f"ORCHESTRATOR | CRUD action returned duplicate: {crud_response.get('message')}") # Novo log
                final_ai_response = crud_response.get("message", "Ação não realizada: item duplicado.")
                response_payload["status"] = "warning"
                logger.info(f"ORCHESTRATOR | User '{user_id}' confirmed action, but detected as duplicate.")
            else:
                logger.error(f"ORCHESTRATOR | CRUD action returned error status: {crud_response}") # Novo log
                final_ai_response = crud_response.get("message", "Houve um erro ao executar a ação confirmada.")
                response_payload["status"] = "error"
                logger.error(f"ORCHESTRATOR | User '{user_id}' confirmed action, but CRUD failed: {crud_response}")

            # MODIFIED: Use clear_confirmation_state from firestore_utils
            try:
                logger.debug(f"ORCHESTRATOR | Attempting to clear confirmation state for user '{user_id}' after positive confirmation.")
                await clear_confirmation_state(user_id) # MODIFIED
                logger.info(f"ORCHESTRATOR | Confirmation state explicitly cleared for user '{user_id}' after positive confirmation.")
            except Exception as e:
                logger.error(f"ORCHESTRATOR | Failed to explicitly clear confirmation state for user '{user_id}' after positive confirmation: {e}", exc_info=True)

            response_payload["response"] = final_ai_response
            response_payload["debug_info"] = { "intent_detected": payload_to_execute.get('item_type'), "action_confirmed": payload_to_execute.get('action'), "crud_result_status": crud_response.get("status")}
            if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return {"response_payload": response_payload} # <--- RETORNO IMEDIATO APÓS CONFIRMAÇÃO POSITIVA

        # Verifica se é uma resposta de confirmação NEGATIVA
        elif any(keyword in lower_message for keyword in negative_keywords):
            logger.info(f"ORCHESTRATOR | Confirmation Flow: Negative keyword '{lower_message}' detected. Canceling cached action.")
            final_ai_response = "Ok, entendi. Ação cancelada."
            response_payload["status"] = "success"
            
            # MODIFIED: Use clear_confirmation_state from firestore_utils
            try:
                logger.debug(f"ORCHESTRATOR | Attempting to clear confirmation state (rejection) for user '{user_id}'.")
                await clear_confirmation_state(user_id) # MODIFIED
                logger.info(f"ORCHESTRATOR | Confirmation state explicitly cleared (rejection) for user '{user_id}'.")
            except Exception as e:
                logger.error(f"ORCHESTRATOR | Failed to explicitly clear confirmation state (rejection) for user '{user_id}': {e}", exc_info=True)

            response_payload["response"] = final_ai_response + " Como posso ajudar de outra forma?"
            response_payload["debug_info"] = { "intent_detected": "cancellation", "action_confirmed": "cancel" }
            if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return {"response_payload": response_payload} # <--- RETORNO IMEDIATO APÓS CONFIRMAÇÃO NEGATIVA
        
        else: # Mensagem ambígua em estado de confirmação (re-prompt)
            logger.info(f"ORCHESTRATOR | Confirmation Flow: Ambiguous message '{lower_message}'. Re-prompting.")
            response_payload["response"] = stored_confirmation_message
            response_payload["status"] = "awaiting_confirmation"
            if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return {"response_payload": response_payload} # <--- RETORNO IMEDIATO PARA RE-PROMPT


    # --- 6. Lógica Principal de Inferência (SÓ SERÁ EXECUTADA SE NÃO ESTIVER EM CONFIRMAÇÃO PENDENTE) ---
    logger.debug(f"ORCHESTRATOR | Not in confirmation state. Proceeding with main inference flow.")
    
    # 6.1. Processamento de Input para Gemini
    logger.debug(f"ORCHESTRATOR | Calling parse_incoming_input for message: '{user_message_for_processing[:50] if user_message_for_processing else '[no message]'}'") # Novo log
    input_parser_results = await asyncio.to_thread(parse_incoming_input, user_message_for_processing, uploaded_file_data)
    user_prompt_parts = input_parser_results['prompt_parts_for_gemini']
    gemini_model_override = input_parser_results['gemini_model_override']
    gemini_final_model = gemini_vision_model if uploaded_file_data else gemini_text_model
    logger.debug(f"ORCHESTRATOR | Input parsed. Model selected: {gemini_final_model}") # Novo log


    # 6.2. Detecção e Atualização de Configurações de Perfil (Direto)
    logger.debug(f"ORCHESTRATOR | Calling parse_and_update_profile_settings.") # Novo log
    profile_settings_results = await parse_and_update_profile_settings(user_id, user_message_for_processing, user_profile_template_content)
    if profile_settings_results.get("profile_updated"):
        logger.debug(f"ORCHESTRATOR | Profile settings updated directly: {profile_settings_results.get('action_message')}") # Novo log
        direct_action_message = profile_settings_results['action_message']
        user_profile = await get_user_profile_data(user_id, user_profile_template_content) # Recarrega o perfil após a atualização
        response_payload["response"] = direct_action_message
        response_payload["status"] = "success"
        response_payload["debug_info"] = {"intent_detected": "configuracao_perfil", "crud_result_status": "success"}
        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
        return {"response_payload": response_payload} # <--- RETORNO para configuração de perfil

    # 6.3. Extração de Intenções CRUD pela LLM
    # A LLM é chamada aqui, pois não estamos em um fluxo de confirmação.
    logger.debug(f"ORCHESTRATOR | Calling _extract_crud_intent_with_llm.") # Novo log
    crud_intent_data = await _extract_crud_intent_with_llm(user_id, user_message_for_processing, full_history, gemini_api_key, gemini_text_model)
    intent_detected_in_orchestrator = crud_intent_data.get("intent_detected", "conversa")
    logger.debug(f"ORCHESTRATOR | LLM intent extraction result: {intent_detected_in_orchestrator}")


    # 6.4. Processamento de Intenções CRUD (Task ou Project)
    if intent_detected_in_orchestrator in ["task", "project"]:
        logger.debug(f"ORCHESTRATOR | Detected LLM intent for CRUD: {intent_detected_in_orchestrator}.") # Novo log
        item_type = crud_intent_data['intent_detected']
        action = crud_intent_data['action']
        item_details = crud_intent_data['item_details']
        llm_generated_confirmation_message = crud_intent_data['confirmation_message']

        # Lógica para TAREFAS
        if item_type == 'task':
            task_description = item_details.get("name") or item_details.get("description")
            task_date = item_details.get("date")

            if action == 'create' and (not task_description or not task_date):
                response_payload["response"] = "Não consegui extrair todos os detalhes necessários para a tarefa (descrição e data). Por favor, seja mais específico."
                response_payload["status"] = "error"
                if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                return {"response_payload": response_payload}

            corrected_task_date = task_date
            if task_date:
                try:
                    local_tz = pytz.timezone(user_profile.get('timezone', DEFAULT_TIMEZONE))
                    parsed_date_obj = datetime.strptime(task_date, "%Y-%m-%d").date()
                    current_date_today = datetime.now(timezone.utc).date()
                    
                    # CORREÇÃO PARA DATAS NO PASSADO: Se a data for anterior ao dia de hoje, tenta ajustar para o ano corrente ou próximo
                    if parsed_date_obj < current_date_today:
                        test_current_year = parsed_date_obj.replace(year=current_date_today.year)
                        if test_current_year >= current_date_today: # Se no ano atual já passou, mas ainda não chegou
                            corrected_task_date = test_current_year.isoformat()
                            logger.info(f"ORCHESTRATOR | Task date '{parsed_date_obj}' was in the past. Adjusted to {corrected_task_date} (current year).")
                        else: # Já passou no ano atual também, tenta o próximo ano
                            test_next_year = parsed_date_obj.replace(year=current_date_today.year + 1)
                            corrected_task_date = test_next_year.isoformat()
                            logger.info(f"ORCHESTRATOR | Task date '{parsed_date_obj}' was in the past. Adjusted to {corrected_task_date} (next year).")

                except ValueError as ve:
                    logger.warning(f"ORCHESTRATOR | Task date '{corrected_task_date}' from LLM could not be parsed for year correction ({ve}). Using original from LLM as fallback.", exc_info=True)

            completed_status = True if action == 'complete' else (item_details.get('status') == 'completed' if 'status' in item_details else None)
            
            provisional_payload_data = {"description": task_description, "date": corrected_task_date}
            if completed_status is not None: provisional_payload_data["completed"] = completed_status
            
            provisional_payload = {
                "user_id": user_id, # <--- ADICIONADO: Garante que o user_id é passado no payload de confirmação
                "item_type": item_type,
                "action": action if action != 'complete' else 'update',
                "item_id": item_details.get("id"),
                "data": provisional_payload_data
            }

            if action == 'create':
                if corrected_task_date:
                    try:
                        local_tz = pytz.timezone(user_profile.get('timezone', DEFAULT_TIMEZONE))
                        # Tenta parsear a data sem fuso horário para display, então localiza
                        parsed_date_for_display_naive = datetime.fromisoformat(corrected_task_date)
                        parsed_date_for_display = local_tz.localize(parsed_date_for_display_naive)
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

        # Lógica para PROJETOS
        elif item_type == 'project':
            project_name = item_details.get("name")
            if action == 'create' and not project_name:
                response_payload["response"] = "Não consegui extrair o nome do projeto. Por favor, seja mais específico."
                response_payload["status"] = "error"
                if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                return {"response_payload": response_payload}

            provisional_payload = {
                "user_id": user_id, # <--- ADICIONADO: Garante que o user_id é passado no payload de confirmação
                "item_type": item_type,
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

        # MODIFIED: Save confirmation state using set_confirmation_state
        await set_confirmation_state( # MODIFIED
            user_id,
            {
                'awaiting_confirmation': True,
                'confirmation_payload_cache': provisional_payload,
                'confirmation_message': confirmation_message
            }
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
        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        return {"response_payload": response_payload} # <--- RETORNO para intenção CRUD detectada


    # --- 7. Lógica de Conversação Genérica com LLM (Se nenhuma intenção específica foi tratada) ---
    logger.debug(f"ORCHESTRATOR | Not in confirmation state. Proceeding with main inference flow.")
    
    # Prepara histórico e contexto para a LLM genérica
    conversation_history = []
    # Usar apenas os últimos 5 turnos de interação para o contexto do LLM, conforme debug.
    # O full_history é retornado do mais recente para o mais antigo, então inverter antes de pegar os últimos 5
    recent_history_for_llm = full_history[-5:]
    for turn in recent_history_for_llm:
        if turn.get("input"): conversation_history.append({"role": "user", "parts": [{"text": turn.get("input")}]})
        if turn.get("output"): conversation_history.append({"role": "model", "parts": [{"text": turn.get("output")}]})
    debug_info_logs.append(f"History prepared with {len(recent_history_for_llm)} turns for LLM context.")


    current_datetime_utc = datetime.now(timezone.utc)
    day_names_pt = {0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira", 3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo"}
    contexto_temporal = f"--- CONTEXTO TEMPORAL ATUAL ---\nA data atual é {current_datetime_utc.strftime('%Y-%m-%d')} ({day_names_pt[current_datetime_utc.weekday()]}). O ano atual é {current_datetime_utc.year}.\n--- FIM DO CONTEXTO TEMPORAL ---\n\n"
    debug_info_logs.append("Temporal context generated for LLM.")

    # Memória Vetorial (Contextualização de Longo prazo)
    if user_message_for_processing and gcp_project_id and region:
        logger.debug(f"ORCHESTRATOR | Attempting to generate embedding for user query.") # Novo log
        user_query_embedding = await get_embedding(user_message_for_processing, gcp_project_id, region, model_name=EMBEDDING_MODEL_NAME)
        if user_query_embedding:
            relevant_memories = await get_relevant_memories(user_id, user_query_embedding, n_results=5)
            if relevant_memories:
                context_string = "\n".join(["--- CONTEXTO DE MEMÓRIAS RELEVANTES DE LONGO PRAZO:"] + [f"- {mem['content']}" for mem in relevant_memories])
                logger.info(f"ORCHESTRATOR | Adding {len(relevant_memories)} relevant memories to LLM context for user '{user_id}'.")
                # Insere no início do histórico para dar mais peso
                conversation_history.insert(0, {"role": "user", "parts": [{"text": context_string}]})
        else:
            logger.warning(f"ORCHESTRATOR | Could not generate embedding for user message. Skipping vector memory retrieval.", exc_info=True)
            debug_info_logs.append("Warning: Embedding generation failed, vector memory not used.")

    # Adiciona a mensagem atual do usuário (já processada) ao histórico da conversa para o LLM
    conversation_history.append({"role": "user", "parts": user_prompt_parts})

    # Constrói o contexto crítico de tarefas e projetos
    contexto_critico = "--- TAREFAS PENDENTES E PROJETOS ATIVOS DO USUÁRIO ---\n"
    logger.debug(f"ORCHESTRATOR | Fetching all daily tasks and projects for critical context.") # Novo log
    current_tasks = await get_all_daily_tasks(user_id)
    flat_current_tasks = []
    # Transforma o dicionário de tarefas diárias em uma lista plana para facilitar a leitura pelo LLM
    for date_key, day_data in current_tasks.items():
        for task_data in day_data.get('tasks', []):
            status = 'Concluída' if task_data.get('completed', False) else 'Pendente'
            flat_current_tasks.append(f"- {task_data.get('description', 'N/A')} (Data: {date_key}, Status: {status})")

    current_projects = await get_all_projects(user_id)
    # Transforma a lista de projetos em uma lista formatada para o LLM
    formatted_projects = []
    for project in current_projects:
        status = project.get('status', 'N/A')
        deadline = project.get('deadline', 'N/A')
        formatted_projects.append(f"- {project.get('name', 'N/A')} (Status: {status}, Prazo: {deadline})")


    if flat_current_tasks:
        contexto_critico += "Tarefas Pendentes:\n" + "\n".join(flat_current_tasks) + "\n"
    else: contexto_critico += "Nenhuma tarefa pendente registrada.\n"
    
    if formatted_projects:
        contexto_critico += "\nProjetos Ativos:\n" + "\n".join(formatted_projects) + "\n"
    else: contexto_critico += "\nNenhum projeto ativo registrado.\n"
    contexto_critico += "--- FIM DO CONTEXTO CRÍTICO ---\n\n"
    debug_info_logs.append("Critical context generated for LLM.")


    # Constrói o contexto de perfil (detalhado)
    base_persona_with_name = base_eixa_persona_template_text.replace("{{user_display_name}}", user_display_name)
    contexto_perfil_str = f"--- CONTEXTO DO PERFIL DO USUÁRIO ({user_display_name}):\n"
    profile_summary_parts = []
    # Adicionando todos os campos relevantes do perfil de forma legível para o LLM
    if user_profile.get('psychological_profile'):
        psych = user_profile['psychological_profile']
        if psych.get('personality_traits') and isinstance(psych['personality_traits'], list) and psych['personality_traits']: profile_summary_parts.append(f"   - Traços de Personalidade: {', '.join(psych['personality_traits'])}")
        if psych.get('diagnoses_and_conditions') and isinstance(psych['diagnoses_and_conditions'], list) and psych['diagnoses_and_conditions']: profile_summary_parts.append(f"   - Condições/Diagnósticos: {', '.join(psych['diagnoses_and_conditions'])}")
        if psych.get('historical_behavioral_patterns') and isinstance(psych['historical_behavioral_patterns'], list) and psych['historical_behavioral_patterns']: profile_summary_parts.append(f"   - Padrões Comportamentais Históricos: {', '.join(psych['historical_behavioral_patterns'])}")
        if psych.get('coping_mechanisms') and isinstance(psych['coping_mechanisms'], list) and psych['coping_mechanisms']: profile_summary_parts.append(f"   - Mecanismos de Coping: {', '.join(psych['coping_mechanisms'])}")

    if user_profile.get('cognitive_style') and isinstance(user_profile['cognitive_style'], list) and user_profile['cognitive_style']:
        profile_summary_parts.append(f"   - Estilo Cognitivo: {', '.join(user_profile['cognitive_style'])}")

    if user_profile.get('communication_preferences'):
        comm_pref = user_profile['communication_preferences']
        if comm_pref.get('tone_preference'): profile_summary_parts.append(f"   - Preferências de Comunicação (Tom): {comm_pref['tone_preference']}")
        if comm_pref.get('intervention_style'): profile_summary_parts.append(f"   - Preferências de Comunicação (Estilo de Intervenção): {comm_pref['intervention_style']}")
        if comm_pref.get('specific_no_gos') and isinstance(comm_pref['specific_no_gos'], list) and comm_pref['specific_no_gos']: profile_summary_parts.append(f"   - Regras Específicas para EIXA (NÃO FAZER): {'; '.join(comm_pref['specific_no_gos'])}")

    if user_profile.get('current_projects') and isinstance(user_profile['current_projects'], list) and user_profile['current_projects']:
        # Note: current_projects aqui é do perfil, pode ser diferente dos projetos ativos no DB.
        # Ajuste para incluir apenas os nomes, se for o caso.
        project_names_from_profile = [p.get('name', 'N/A') for p in user_profile['current_projects'] if isinstance(p, dict)]
        if project_names_from_profile: profile_summary_parts.append(f"   - Projetos Atuais (do perfil): {', '.join(project_names_from_profile)}")

    # Metas (long_term, medium_term, short_term)
    if user_profile.get('goals', {}) and isinstance(user_profile['goals'], dict):
        for term_type in ['long_term', 'medium_term', 'short_term']:
            if user_profile['goals'].get(term_type) and isinstance(user_profile['goals'][term_type], list) and user_profile['goals'][term_type]:
                goals_text = [g.get('value', 'N/A') for g in user_profile['goals'][term_type] if isinstance(g, dict) and g.get('value')]
                if goals_text: profile_summary_parts.append(f"   - Metas de {'Longo' if term_type == 'long_term' else 'Médio' if term_type == 'medium_term' else 'Curto'} Prazo: {', '.join(goals_text)}")

    if user_profile.get('eixa_interaction_preferences', {}).get('expected_eixa_actions') and isinstance(user_profile['eixa_interaction_preferences']['expected_eixa_actions'], list) and user_profile['eixa_interaction_preferences']['expected_eixa_actions']:
        actions_text = user_profile['eixa_interaction_preferences']['expected_eixa_actions']
        profile_summary_parts.append(f"   - Ações Esperadas da EIXA: {', '.join(actions_text)}")
    
    # Elementos de rotina diária
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

        # Alertas e Lembretes
        if daily_routine.get('alerts_and_reminders'):
            alerts_rem = daily_routine['alerts_and_reminders']
            if alerts_rem.get('hydration'): daily_routine_list.append(f"Alerta Hidratação: {alerts_rem['hydration']}")
            if alerts_rem.get('eye_strain'): daily_routine_list.append(f"Alerta Fadiga Visual: {alerts_rem['eye_strain']}")
            if alerts_rem.get('mobility'): daily_routine_list.append(f"Alerta Mobilidade: {alerts_rem['mobility']}")
            if alerts_rem.get('mindfulness'): daily_routine_list.append(f"Alerta Mindfulness: {alerts_rem['mindfulness']}")
            if alerts_rem.get('meal_times'): daily_routine_list.append(f"Alerta Refeições: {', '.join(alerts_rem['meal_times'])}")
            if alerts_rem.get('medication_reminders') and isinstance(alerts_rem['medication_reminders'], list) and alerts_rem['medication_reminders']: daily_routine_list.append(f"Alerta Medicação: {', '.join(alerts_rem['medication_reminders'])}")
            if alerts_rem.get('overwhelm_triggers') and isinstance(alerts_rem['overwhelm_triggers'], list) and alerts_rem['overwhelm_triggers']: daily_routine_list.append(f"Gatilhos Sobrecarga: {', '.join(alerts_rem['overwhelm_triggers'])}")
            if alerts_rem.get('burnout_indicators') and isinstance(alerts_rem['burnout_indicators'], list) and alerts_rem['burnout_indicators']: daily_routine_list.append(f"Indicadores Burnout: {', '.join(alerts_rem['burnout_indicators'])}")
        
        if daily_routine_list: profile_summary_parts.append(f"   - Elementos da Rotina Diária: {'; '.join(daily_routine_list)}")
    
    # Adicionar dados de Consentimento
    if user_profile.get('data_usage_consent') is not None:
        profile_summary_parts.append(f"   - Consentimento de Uso de Dados: {'Concedido' if user_profile['data_usage_consent'] else 'Não Concedido'}")
    
    # Outros campos diretos
    if user_profile.get('locale'): profile_summary_parts.append(f"   - Localidade: {user_profile['locale']}")
    if user_profile.get('timezone'): profile_summary_parts.append(f"   - Fuso Horário: {user_profile['timezone']}")
    if user_profile.get('age_range'): profile_summary_parts.append(f"   - Faixa Etária: {user_profile['age_range']}")
    if user_profile.get('gender_identity'): profile_summary_parts.append(f"   - Gênero: {user_profile['gender_identity']}")
    if user_profile.get('education_level'): profile_summary_parts.append(f"   - Nível Educacional: {user_profile['education_level']}")


    contexto_perfil_str += "\n".join(profile_summary_parts) if profile_summary_parts else "   Nenhum dado de perfil detalhado disponível.\n"
    contexto_perfil_str += "--- FIM DO CONTEXTO DE PERFIL ---\n\n"

    final_system_instruction = contexto_temporal + contexto_critico + contexto_perfil_str + base_persona_with_name

    # Chamada LLM genérica
    logger.debug(f"ORCHESTRATOR | Calling Gemini API for generic response. Model: {gemini_final_model}") # Novo log
    gemini_response_text_in_pt = await call_gemini_api(
        api_key=gemini_api_key, model_name=gemini_final_model, conversation_history=conversation_history,
        system_instruction=final_system_instruction, max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        temperature=DEFAULT_TEMPERATURE
    )

    final_ai_response = gemini_response_text_in_pt

    if not final_ai_response:
        final_ai_response = "Não consegui processar sua solicitação no momento. Tente novamente."
        response_payload["status"] = "error"
        logger.error(f"ORCHESTRATOR | Gemini response was None or empty for user '{user_id}'. Setting response_payload status to 'error'.", exc_info=True)
    else:
        profile_update_json = None
        # Tenta extrair um bloco JSON da resposta do LLM para atualização de perfil
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', final_ai_response, re.DOTALL)
        if json_match:
            try:
                profile_update_json_str = json_match.group(1)
                profile_update_data = json.loads(profile_update_json_str)
                profile_update_json = profile_update_data.get('profile_update') # Espera que o JSON tenha uma chave 'profile_update'

                # CORREÇÃO ROBUSTA para remover o JSON da resposta:
                # Extrai o texto antes e depois do bloco JSON COM os backticks
                pre_json_text = final_ai_response[:json_match.start()].strip()
                post_json_text = final_ai_response[json_match.end():].strip()
                
                # Reconstrói a resposta sem o bloco JSON, garantindo que não haja quebras de linha excessivas
                final_ai_response = (pre_json_text + "\n\n" + post_json_text).strip()
                final_ai_response = final_ai_response.replace('\n\n\n', '\n\n') # Limpa quebras de linha triplas, se houver
                
                logger.info(f"ORCHESTRATOR | Detected profile_update JSON from LLM for user '{user_id}'.")
            except json.JSONDecodeError as e:
                logger.warning(f"ORCHESTRATOR | Failed to parse profile_update JSON from LLM: {e}. Raw JSON: {json_match.group(1)[:100]}...", exc_info=True)
            except AttributeError as e:
                logger.warning(f"ORCHESTRATOR | Profile update JSON missing 'profile_update' key or has unexpected structure: {e}. Raw data: {profile_update_data}", exc_info=True)

        # Tradução da resposta da IA de volta para o idioma original do usuário, se necessário
        if source_language != "pt":
            logger.info(f"ORCHESTRATOR | Translating AI response from 'pt' to '{source_language}' for user '{user_id}'. Original: '{final_ai_response[:50]}...'.")
            translated_ai_response = await translate_text(final_ai_response, source_language, "pt")

            if translated_ai_response is None:
                logger.error(f"ORCHESTRATOR | Translation of AI response failed for user '{user_id}'. Original PT: '{final_ai_response}'. Target language: '{source_language}'.", exc_info=True)
                fallback_error_msg_pt = "Ocorreu um problema ao traduzir minha resposta. Por favor, tente novamente."
                # Tenta traduzir a mensagem de erro de fallback também
                translated_fallback = await translate_text(fallback_error_msg_pt, source_language, "pt")
                final_ai_response = translated_fallback if translated_fallback is not None else fallback_error_msg_pt
                response_payload["status"] = "error"
            else:
                final_ai_response = translated_ai_response
            logger.info(f"ORCHESTRATOR | AI response after translation: '{final_ai_response[:50]}...'.")
        else:
            logger.info(f"ORCHESTRATOR | No translation needed for AI response (source_language is 'pt') for user '{user_id}'.")

    response_payload["response"] = final_ai_response
    # Ajusta o status do payload de resposta se a IA gerou uma mensagem de erro de fallback
    if response_payload["status"] == "success" and ("Não consegui processar sua solicitação" in final_ai_response or "Ocorreu um problema ao traduzir" in final_ai_response):
        response_payload["status"] = "error"
        logger.warning(f"ORCHESTRATOR | Response for user '{user_id}' contained a fallback error message, forcing status to 'error'.")

    # Salva a interação completa (input do usuário e output da EIXA) no Firestore
    await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
    logger.info(f"ORCHESTRATOR | Interaction saved for user '{user_id}'. Final response status: {response_payload['status']}.")

    # Se o LLM inferiu dados para atualizar o perfil, aplica-os
    if profile_update_json:
        await update_profile_from_inferred_data(user_id, profile_update_json, user_profile_template_content)
        logger.info(f"ORCHESTRATOR | Profile updated based on LLM inference for user '{user_id}'.")

    # Gera e salva embeddings da interação para memória vetorial
    if user_message_for_processing and final_ai_response and gcp_project_id and region:
        # Erros de quota para embeddings são logados aqui, mas não impedem o fluxo principal se o embedding não for gerado.
        logger.debug(f"ORCHESTRATOR | Attempting to generate embedding for interaction.") # Novo log
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

    # Lógica de detecção de emoções e padrões de sabotagem para adicionar tags
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

    # Lógica de "nudges" (sugestões proativas)
    nudge_message = await analyze_for_nudges(
        user_id, user_message_for_processing, full_history, user_flags_data,
        user_profile=user_profile
    )
    if nudge_message:
        # Adiciona a mensagem de nudge no início da resposta da EIXA
        response_payload["response"] = nudge_message + "\n\n" + response_payload["response"]
        logger.info(f"ORCHESTRATOR | Generated nudge for user '{user_id}': '{nudge_message[:50]}...'.")

    # A detecção de sabotagem foi chamada antes. A exibição é feita no final, se houver padrões.
    # Filtra padrões que ocorreram 2 ou mais vezes, por exemplo
    filtered_patterns = {p: f for p, f in sabotage_patterns_detected.items() if f >= 2}
    if filtered_patterns:
        # Adiciona a mensagem de padrões de sabotagem no final da resposta da EIXA
        response_payload["response"] += "\n\n⚠️ **Padrões de auto-sabotagem detectados:**\n" + "\n".join(f"- \"{p}\" ({str(f)} vezes)" for p, f in filtered_patterns.items())
        logger.info(f"ORCHESTRATOR | Detected and added {len(filtered_patterns)} sabotage patterns to response for user '{user_id}'.")

    # Adiciona informações de depuração ao payload final, se o modo debug estiver ativado
    if mode_debug_on:
        if "orchestrator_debug_log" not in response_payload["debug_info"]:
            response_payload["debug_info"]["orchestrator_debug_log"] = []
        response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        response_payload["debug_info"]["user_profile_loaded"] = 'true' if user_profile else 'false'
        response_payload["debug_info"]["user_flags_loaded"] = 'true' if user_flags_data else 'false'
        response_payload["debug_info"]["generated_nudge"] = 'true' if nudge_message else 'false'
        response_payload["debug_info"]["system_instruction_snippet"] = final_system_instruction[:500] + "..."

    return {"response_payload": response_payload} # <-- Retorno final da função
