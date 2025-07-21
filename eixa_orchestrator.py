import logging
import asyncio
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Dict, Any, List
import re
import json
import pytz

# Imports de lógica de negócio e utilitários
from memory_utils import (
    add_emotional_memory,
    get_emotional_memories,
    get_sabotage_patterns,
)
# task_manager e project_manager serão menos usados para CRUD direto, mais para parsing genérico.
# As funções CRUD reais serão roteadas para eixa_data via este orquestrador.
# Removeremos estas importações se não forem mais usadas diretamente
# from task_manager import parse_and_update_agenda_items, parse_and_update_project_items 
from eixa_data import (
    get_daily_tasks_data, save_daily_tasks_data, get_project_data, save_project_data, 
    get_user_history, get_all_projects,
    get_all_routines, save_routine_template, apply_routine_to_day, delete_routine_template, get_routine_template,
    sync_google_calendar_events_to_eixa
)

from vertex_utils import call_gemini_api
from vectorstore_utils import get_embedding, add_memory_to_vectorstore, get_relevant_memories

# Importações de firestore_utils para operar com o Firestore
from firestore_utils import (
    get_user_profile_data,
    get_firestore_document_data,
    set_firestore_document,
    save_interaction,
    get_confirmation_state,
    set_confirmation_state,
    clear_confirmation_state,
    set_firestore_document_merge # Adicionada para atualizar partes do documento
)
from google.cloud import firestore

from nudger import analyze_for_nudges
from user_behavior import track_repetition
from personal_checkpoint import get_latest_self_eval
from translation_utils import detect_language, translate_text

from config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_TEMPERATURE, DEFAULT_TIMEZONE, USERS_COLLECTION, TOP_LEVEL_COLLECTIONS_MAP, GEMINI_VISION_MODEL, GEMINI_TEXT_MODEL, EMBEDDING_MODEL_NAME

from input_parser import parse_incoming_input
from app_config_loader import get_eixa_templates
from crud_orchestrator import orchestrate_crud_action
from profile_settings_manager import parse_and_update_profile_settings, update_profile_from_inferred_data

from google_calendar_utils import GoogleCalendarUtils, GOOGLE_CALENDAR_SCOPES

logger = logging.getLogger(__name__)

google_calendar_auth_manager = GoogleCalendarUtils()

async def _extract_llm_action_intent(user_id: str, user_message: str, history: list, gemini_api_key: str, gemini_text_model: str, user_profile: Dict[str, Any], all_routines: List[Dict[str, Any]]) -> dict | None:
    """
    Extrai intenções de ação (CRUD, Rotinas, Google Calendar) da mensagem do usuário usando o LLM.
    """
    current_date_utc = datetime.now(timezone.utc).date()
    current_date_iso = current_date_utc.isoformat()
    tomorrow_date_iso = (current_date_utc + timedelta(days=1)).isoformat()
    one_week_later_iso = (current_date_utc + timedelta(days=7)).isoformat() # Adicionado para sync GC

    # Prepara a lista de rotinas do usuário para o LLM
    routines_list_for_llm = []
    for routine in all_routines:
        # Inclui o schedule no summary da rotina para o LLM ter mais contexto
        schedule_summary = []
        for item in routine.get('schedule', []):
            schedule_summary.append(f"({item.get('time', 'N/A')} - {item.get('description', 'N/A')})")
        
        routines_list_for_llm.append(f"- Nome: {routine.get('name')}, ID: {routine.get('id')}, Descrição: {routine.get('description', 'N/A')}. Tarefas: {', '.join(schedule_summary)}")
    
    routines_context = ""
    if routines_list_for_llm:
        routines_context = "\nRotinas existentes:\n" + "\n".join(routines_list_for_llm) + "\n"

    system_instruction_for_action_extraction = f"""
    A data atual é {current_date_iso}. O fuso horário do usuário é {user_profile.get('timezone', DEFAULT_TIMEZONE)}.
    Você é um assistente de extração de intenções altamente preciso e sem vieses. Sua função é analisar **EXCLUSIVAMENTE a última mensagem do usuário** para identificar INTENÇÕES CLARAS e DIRETAS de CRIAÇÃO, ATUALIZAÇÃO, EXCLUSÃO, MARCAÇÃO DE CONCLUSÃO (COMPLETE) de TAREFAS OU PROJETOS, ou GERENCIAMENTO de ROTINAS/CALENDÁRIOS.

    **REGRAS RÍGIDAS DE SAÍDA:**
    1.  **SEMPRE** retorne APENAS um bloco JSON, sem texto conversacional.
    2.  **PRIORIDADE ABSOLUTA:** Se a mensagem do usuário for uma resposta simples de confirmação ou negação (e.g., "Sim", "Não", "Certo", "Ok", "Por favor", "Deletar!", "Adicionar!", "Cancelar", "Concluir!", "Entendido", "Faça", "Prossiga", "Não quero", "Obrigado", "Bom dia", "Não sei por onde começar", "O que é EIXA?"), **VOCÊ DEVE RETORNAR SOMENTE:**
        ```json
        {{
        "intent_detected": "none"
        }}
        ```
        Não tente interpretar essas mensagens como novas intenções de CRUD/Gerenciamento. Elas são respostas a uma pergunta anterior.
    3.  Se uma intenção de tarefa, projeto, rotina ou calendário for detectada **CLARAMENTE** na ÚLTIMA MENSAGEM (e não for uma resposta de confirmação/negação), retorne um JSON com a seguinte estrutura.

    **ESTRUTURA DE SAÍDA DETALHADA:**
    ```json
    {{
    "intent_detected": "task" | "project" | "routine" | "google_calendar" | "none",
    "action": "create" | "update" | "delete" | "complete" | "apply_routine" | "sync_calendar" | "connect_calendar" | "disconnect_calendar",
    "item_details": {{
        // Campos comuns para Task/Project/Routine Item
        "id": "ID_DO_ITEM_SE_FOR_UPDATE_OU_DELETE_OU_APPLY_ROUTINE",
        "name": "Nome do projeto ou rotina",
        "description": "Descrição da tarefa ou da rotina",
        "date": "YYYY-MM-DD" | null,
        "time": "HH:MM" | null,
        "duration_minutes": int | null,
        "completed": true | false | null,
        "status": "open" | "completed" | "in_progress" | null,

        // Campos específicos para 'routine'
        "routine_name": "Nome da Rotina (ex: Rotina Matinal)",
        "routine_description": "Descrição da rotina (ex: Rotina de trabalho das 9h às 18h)",
        "days_of_week": ["MONDAY", "TUESDAY", ...] | null,
        "schedule": [
            {{"id": "UUID_DO_ITEM_NA_ROTINA", "time": "HH:MM", "description": "Descrição da atividade", "duration_minutes": int, "type": "task"}}
        ] | null,

        // Campos específicos para 'google_calendar'
        "start_date": "YYYY-MM-DD",
        "end_date": "YYYY-MM-DD",
        "calendar_id": "primary"
    }},
    "confirmation_message": "Confirma que deseja...?"
    }}
    ```
    **Regras para Datas, Horas e Duração:**
    - Para datas, use YYYY-MM-DD. **"hoje" DEVE ser {current_date_iso}. "amanhã" DEVE ser {tomorrow_date_iso}.** "próxima segunda" DEVE ser a data da próxima segunda-feira no formato YYYY-MM-DD. Se nenhuma data for clara, use `null`.
    - Para horários, use HH:MM. Se o usuário disser "às 2 da tarde", use "14:00". Se não for claro, use `null`.
    - Para duração, use `duration_minutes` como um número inteiro. "por uma hora" = `60`. "por meia hora" = `30`.

    **EXEMPLOS DE INTENÇÕES E SAÍDAS:**
    - Usuário: "Crie uma rotina de estudo para mim. Das 9h às 10h estudar python, 10h-10h30 pausa, 10h30-12h fazer exercícios."
      ```json
      {{
      "intent_detected": "routine",
      "action": "create",
      "item_details": {{
          "routine_name": "Rotina de Estudo",
          "routine_description": "Plano de estudo customizado.",
          "schedule": [
              {{"id": "UUID_GERADO_PELO_LLM", "time": "09:00", "description": "Estudar Python", "duration_minutes": 60, "type": "task"}},
              {{"id": "UUID_GERADO_PELO_LLM", "time": "10:00", "description": "Pausa", "duration_minutes": 30, "type": "break"}},
              {{"id": "UUID_GERADO_PELO_LLM", "time": "10:30", "description": "Fazer exercícios", "duration_minutes": 90, "type": "task"}}
          ]
      }},
      "confirmation_message": "Confirma a criação da rotina 'Rotina de Estudo' com esses horários?"
      }}
      ```
    - Usuário: "Aplique minha 'Rotina Matinal' para amanhã."
      ```json
      {{
      "intent_detected": "routine",
      "action": "apply_routine",
      "item_details": {{
          "id": "ID_DA_ROTINA_MATINAL_DO_USUARIO_SE_EXISTIR",
          "routine_name": "Rotina Matinal"
      }},
      "date": "{tomorrow_date_iso}",
      "confirmation_message": "Confirma a aplicação da 'Rotina Matinal' para amanhã?"
      }}
      ```
    - Usuário: "Sincronize meu Google Calendar para a próxima semana."
      ```json
      {{
      "intent_detected": "google_calendar",
      "action": "sync_calendar",
      "item_details": {{
          "start_date": "{current_date_iso}",
          "end_date": "{current_date_iso + timedelta(days=7).isoformat()}"
      }},
      "confirmation_message": "Deseja que eu puxe os eventos do seu Google Calendar para a próxima semana?"
      }}
      ```
    - Usuário: "Conecte-me ao meu Google Calendar."
      ```json
      {{
      "intent_detected": "google_calendar",
      "action": "connect_calendar",
      "item_details": {{
          // `redirect_url` será preenchido pelo backend, não pelo LLM.
      }},
      "confirmation_message": "Você gostaria de conectar a EIXA ao seu Google Calendar? Isso me permitirá ver seus eventos e ajudá-lo melhor."
      }}
      ```
    """ + routines_context

    logger.debug(f"_extract_llm_action_intent: Processing message '{user_message[:50]}...' for CRUD/Routine/Calendar intent.")
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
            system_instruction=system_instruction_for_action_extraction,
            max_output_tokens=1024,
            temperature=0.1
        )

        if llm_response_raw:
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', llm_response_raw, re.DOTALL)
            if json_match:
                extracted_data = json.loads(json_match.group(1))
                logger.debug(f"ORCHESTRATOR | LLM extracted Action intent: {extracted_data}")
                return extracted_data
            else:
                logger.warning(f"ORCHESTRATOR | LLM did not return valid JSON for action extraction. Raw response: {llm_response_raw[:200]}...", exc_info=True)
        return {"intent_detected": "none"}
    except Exception as e:
        logger.error(f"ORCHESTRATOR | Error during LLM action intent extraction for user '{user_id}': {e}", exc_info=True)
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
        
        confirmation_state_data = await get_confirmation_state(user_id)
        is_in_confirmation_state = confirmation_state_data.get('awaiting_confirmation', False)
        confirmation_payload_cache = confirmation_state_data.get('confirmation_payload_cache', {})
        stored_confirmation_message = confirmation_state_data.get('confirmation_message', "Aguardando sua confirmação. Por favor, diga 'sim' ou 'não'.")
        
        logger.debug(f"ORCHESTRATOR_START | User '{user_id}' req: '{user_message[:50] if user_message else '[no message]'}' | State: is_in_confirmation_state={is_in_confirmation_state}, confirmation_payload_cache_keys={list(confirmation_payload_cache.keys()) if confirmation_payload_cache else 'None'}. Loaded confirmation_state_data={confirmation_state_data}")

        user_flags_data_raw = await get_firestore_document_data('flags', user_id)
        user_flags_data = user_flags_data_raw.get("behavior_flags", user_flags_template_content) if user_flags_data_raw else user_flags_template_content
        if not user_flags_data_raw:
            await set_firestore_document('flags', user_id, {"behavior_flags": user_flags_data})

        all_routines = await get_all_routines(user_id)
        logger.debug(f"ORCHESTRATOR | Loaded {len(all_routines)} routines for user {user_id}.")

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
        logger.debug(f"ORCHESTRATOR | Processing view_request: {view_request}")
        if view_request == "agenda":
            agenda_data = await get_all_daily_tasks(user_id)
            response_payload["html_view_data"]["agenda"] = agenda_data
            response_payload["response"] = "Aqui estão suas tarefas."
        elif view_request == "projetos":
            projects_data = await get_all_projects(user_id)
            response_payload["html_view_data"]["projetos"] = projects_data
            response_payload["response"] = "Aqui está a lista dos seus projetos."
        # NOVO: View para TEMPLATES de rotina (acessada pelo botão no Perfil)
        elif view_request == "rotinas_templates_view":
            response_payload["html_view_data"]["routines"] = all_routines
            response_payload["response"] = "Aqui estão seus templates de rotina."
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
                response_payload["status"] = "info"
                response_payload["response"] = "A exibição do seu perfil completo na memória de longo prazo está desativada. Se desejar ativá-la, por favor me diga 'mostrar meu perfil'."
                logger.info(f"ORCHESTRATOR | Long-term memory (profile) requested but display is disabled for user '{user_id}'.")
        # NOVO: View Request para verificar status de conexão do Google Calendar
        elif view_request == "google_calendar_connection_status":
            is_connected = await google_calendar_auth_manager.get_credentials(user_id) is not None
            response_payload["html_view_data"]["google_calendar_connected_status"] = is_connected
            response_payload["response"] = f"Status de conexão Google Calendar: {'Conectado' if is_connected else 'Não Conectado'}."
            logger.info(f"ORCHESTRATOR | Google Calendar connection status requested. Is Connected: {is_connected}")
        else:
            response_payload["status"] = "error"
            response_payload["response"] = "View solicitada inválida."

        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        return {"response_payload": response_payload}

    # --- 3. Verificação de Mensagem Vazia ---
    if not user_message and not uploaded_file_data:
        logger.debug("ORCHESTRATOR | No user message or file data provided.")
        response_payload["status"] = "error"
        response_payload["response"] = "Nenhuma mensagem ou arquivo fornecido para interação."
        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        return {"response_payload": response_payload}

    # --- 4. Preparação da Mensagem (Idioma, Histórico) ---
    user_input_for_saving = user_message or (uploaded_file_data.get('filename') if uploaded_file_data else "Ação do sistema")
    source_language = await detect_language(user_message or "Olá")
    response_payload["language"] = source_language
    user_message_for_processing = user_message
    logger.debug(f"ORCHESTRATOR | Detected source language: {source_language}")
    if source_language != 'pt' and user_message:
        logger.debug(f"ORCHESTRATOR | Translating user message from {source_language} to pt.")
        translated_user_message = await translate_text(user_message, "pt", source_language)
        if translated_user_message is None:
            response_payload["status"] = "error"
            response_payload["response"] = f"Ocorreu um problema ao traduzir sua mensagem de {source_language}."
            if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return {"response_payload": response_payload}
        user_message_for_processing = translated_user_message
    
    full_history = await get_user_history(user_id, firestore_collection_interactions, limit=20)
    logger.debug(f"ORCHESTRATOR | Full history retrieved, {len(full_history)} turns. History for LLM: {full_history[-5:]}")

    # --- 5. LÓGICA DE CONFIRMAÇÃO PENDENTE (MAIOR PRIORIDADE AQUI!) ---
    if is_in_confirmation_state and confirmation_payload_cache:
        logger.debug(f"ORCHESTRATOR | Entered confirmation state logic path. Cached action: {confirmation_payload_cache.get('action')}")
        lower_message = user_message_for_processing.lower().strip()

        confirmation_keywords = [
            "sim", "ok", "confirmo", "confirma", "adicione", "crie", "pode",
            "certo", "beleza", "isso", "deletar", "excluir", "remover",
            "concluir", "finalizar", "ok, faça",
            "sim, por favor", "sim por favor", "claro", "definitivamente",
            "vai", "fazer", "execute", "prossiga", "adiante",
            "sincronizar", "conectar", "desconectar" # Novas palavras-chave de confirmação
        ]
        negative_keywords = ["não", "nao", "cancela", "esquece", "pare", "não quero", "nao quero", "negativo", "desisto"]

        if any(keyword in lower_message for keyword in confirmation_keywords):
            logger.info(f"ORCHESTRATOR | Confirmation Flow: Positive keyword '{lower_message}' detected. Attempting to execute cached action.")
            payload_to_execute = confirmation_payload_cache
            
            action_type = payload_to_execute.get('action') # "create" | "update" | "delete" | "apply_routine" | "sync_calendar" | "connect_calendar" | "disconnect_calendar"
            item_type = payload_to_execute.get('item_type') # "task" | "project" | "routine" | "google_calendar"

            result = {"status": "error", "message": "Ação não reconhecida no fluxo de confirmação."} # Default
            html_view_update = {} # Para coletar as atualizações de HTML

            try:
                if item_type in ["task", "project"]:
                    # Roteia para o crud_orchestrator existente
                    result = await orchestrate_crud_action(payload_to_execute)
                    if result.get('html_view_data'):
                        html_view_update = result['html_view_data']
                    # Garante que, se for uma tarefa, a agenda seja recarregada (crud_orchestrator já faz isso, mas reforço)
                    elif item_type == "task":
                        html_view_update["agenda"] = await get_all_daily_tasks(user_id)
                    elif item_type == "project":
                        html_view_update["projetos"] = await get_all_projects(user_id)

                elif item_type == "routine":
                    # Ajuste aqui para usar 'data' do payload, que já tem routine_name, schedule, etc.
                    routine_data = payload_to_execute.get('data', {}) 
                    routine_name = routine_data.get('routine_name')
                    # Para 'apply_routine', a data está no nível superior do payload
                    target_date_for_apply = payload_to_execute.get('date') 

                    if action_type == "create":
                        # O ID da rotina para save_routine_template é gerado dentro dela se não houver um
                        # Mas o LLM agora gera IDs para os itens da rotina (schedule)
                        routine_id_from_payload = payload_to_execute.get('item_id') # Usa o item_id do payload principal
                        if not routine_id_from_payload: routine_id_from_payload = str(uuid.uuid4()) # Fallback se LLM não gerou

                        # Garante que os IDs dos itens da rotina (schedule) são strings
                        if 'schedule' in routine_data and isinstance(routine_data['schedule'], list):
                            for item in routine_data['schedule']:
                                if 'id' not in item or not isinstance(item['id'], str):
                                    item['id'] = str(uuid.uuid4())

                        await save_routine_template(user_id, routine_id_from_payload, routine_data)
                        result = {"status": "success", "message": f"Rotina '{routine_name}' criada com sucesso!"}
                        html_view_update["routines"] = await get_all_routines(user_id) # Atualiza a lista de rotinas templates
                    elif action_type == "apply_routine":
                        routine_name_or_id = payload_to_execute.get('item_id') # item_id do payload principal (ID ou nome da rotina)
                        
                        if not routine_name_or_id or not target_date_for_apply:
                            result = {"status": "error", "message": "Não foi possível aplicar a rotina: dados incompletos (nome/ID ou data)."}
                        else:
                            # A função apply_routine_to_day precisa buscar o template se for passado só o nome/ID
                            apply_result = await apply_routine_to_day(user_id, target_date_for_apply, routine_name_or_id)
                            result = {"status": apply_result.get("status"), "message": apply_result.get("message", f"Rotina aplicada para {target_date_for_apply} com sucesso!")}
                            if result.get("status") == "success":
                                html_view_update["agenda"] = await get_all_daily_tasks(user_id) # Atualiza a agenda
                    elif action_type == "delete":
                        routine_name_or_id_to_delete = payload_to_execute.get('item_id') # item_id do payload principal (ID ou nome da rotina)
                        if routine_name_or_id_to_delete:
                            delete_result = await delete_routine_template(user_id, routine_name_or_id_to_delete)
                            result = {"status": delete_result.get("status"), "message": delete_result.get("message", "Rotina excluída com sucesso!")}
                            if result.get("status") == "success":
                                html_view_update["routines"] = await get_all_routines(user_id) # Atualiza a lista de rotinas templates
                        else:
                            result = {"status": "error", "message": "Não foi possível excluir a rotina: ID/Nome não fornecido."}
                    else:
                        logger.warning(f"ORCHESTRATOR | Unhandled routine action: {action_type} for user {user_id}")
                        result = {"status": "error", "message": "Ação de rotina não suportada."}
                
                elif item_type == "google_calendar":
                    sync_details = payload_to_execute.get('data', {}) # Pega os detalhes da sincronização de 'data'
                    
                    if action_type == "sync_calendar":
                        start_date_str = sync_details.get('start_date')
                        end_date_str = sync_details.get('end_date')

                        start_date_obj = datetime.fromisoformat(start_date_str) if start_date_str else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                        end_date_obj = datetime.fromisoformat(end_date_str) if end_date_str else start_date_obj + timedelta(days=7) # Padrão: 7 dias

                        # Verificar credenciais ANTES de tentar sincronizar
                        creds = await google_calendar_auth_manager.get_credentials(user_id)
                        if not creds:
                            result = {"status": "info", "message": "Para sincronizar, sua conta Google precisa estar conectada. Quer conectar agora?"}
                            # Setar um novo payload de confirmação para "connect_calendar"
                            await set_confirmation_state(user_id, {
                                'awaiting_confirmation': True,
                                'confirmation_payload_cache': {"user_id": user_id, "item_type": "google_calendar", "action": "connect_calendar", "data": {}},
                                'confirmation_message': "Para sincronizar, preciso que você conecte seu Google Calendar. Confirma que quer conectar agora?"
                            })
                            # Retorna imediatamente para o re-prompt (não para a ação original)
                            response_payload["response"] = stored_confirmation_message
                            response_payload["status"] = "awaiting_confirmation"
                            return {"response_payload": response_payload}

                        sync_result = await sync_google_calendar_events_to_eixa(user_id, start_date_obj, end_date_obj)
                        result = {"status": sync_result.get("status"), "message": sync_result.get("message", "Sincronização com Google Calendar concluída!")}
                        if result.get("status") == "success":
                            html_view_update["agenda"] = await get_all_daily_tasks(user_id)
                    
                    elif action_type == "connect_calendar":
                        # Este bloco é executado QUANDO o usuário confirma que quer conectar.
                        # Aqui, o backend DEVE gerar a URL de autorização e o frontend DEVE redirecionar.
                        try:
                            auth_url = await google_calendar_auth_manager.get_auth_url(user_id)
                            result = {"status": "success", "message": "Por favor, clique no link para conectar seu Google Calendar. Se a janela não abrir automaticamente, copie e cole no seu navegador:", "google_auth_redirect_url": auth_url}
                            logger.info(f"ORCHESTRATOR | Generated Google Auth URL for user {user_id}: {auth_url}")
                            # Não limpa o confirmation state aqui, o frontend deve limpar após o redirect
                        except Exception as e:
                            logger.error(f"ORCHESTRATOR | Failed to generate Google Auth URL for user {user_id}: {e}", exc_info=True)
                            result = {"status": "error", "message": "Não foi possível gerar o link de conexão com o Google Calendar. Tente novamente."}
                    
                    elif action_type == "disconnect_calendar":
                        delete_result = await google_calendar_auth_manager.delete_credentials(user_id)
                        if delete_result.get("status") == "success":
                            result = {"status": "success", "message": "Sua conta Google foi desconectada da EIXA."}
                        else:
                            result = {"status": "error", "message": delete_result.get("message", "Falha ao desconectar a conta Google.")}

            except Exception as e:
                logger.critical(f"ORCHESTRATOR | CRITICAL ERROR executing confirmed action type '{item_type}' with action '{action_type}' for user '{user_id}': {e}", exc_info=True)
                result = {"status": "error", "message": f"Erro interno ao executar a ação confirmada: {str(e)}"}

            final_ai_response = result.get("message", "Ação concluída com sucesso.")
            if result.get("status") == "success":
                # Adiciona URL de redirecionamento se presente no resultado (apenas para connect_calendar)
                if result.get("google_auth_redirect_url"):
                    response_payload["google_auth_redirect_url"] = result["google_auth_redirect_url"]
                    final_ai_response = result["message"] # A mensagem já é específica o suficiente
                else:
                    final_ai_response += " O que mais posso fazer por você?"
            else:
                final_ai_response += " Por favor, tente novamente ou reformule seu pedido."

            response_payload["status"] = result.get("status")
            response_payload["response"] = final_ai_response
            response_payload["html_view_data"] = html_view_update # Inclui as atualizações de HTML

            # Limpa o estado de confirmação após a execução, EXCETO se for 'connect_calendar' bem-sucedido
            # pois o frontend precisa da URL antes de limpar.
            if not (item_type == "google_calendar" and action_type == "connect_calendar" and result.get("status") == "success"):
                await clear_confirmation_state(user_id)
            
            if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return {"response_payload": response_payload}

        elif any(keyword in lower_message for keyword in negative_keywords):
            logger.info(f"ORCHESTRATOR | Confirmation Flow: Negative keyword '{lower_message}' detected. Canceling cached action.")
            final_ai_response = "Ok, entendi. Ação cancelada."
            response_payload["status"] = "success"
            
            try:
                logger.debug(f"ORCHESTRATOR | Attempting to clear confirmation state (rejection) for user '{user_id}'.")
                await clear_confirmation_state(user_id)
                logger.info(f"ORCHESTRATOR | Confirmation state explicitly cleared (rejection) for user '{user_id}'.")
            except Exception as e:
                logger.error(f"ORCHESTRATOR | Failed to explicitly clear confirmation state (rejection) for user '{user_id}': {e}", exc_info=True)

            response_payload["response"] = final_ai_response + " Como posso ajudar de outra forma?"
            response_payload["debug_info"] = { "intent_detected": "cancellation", "action_confirmed": "cancel" }
            if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return {"response_payload": response_payload}
        
        else: # Mensagem ambígua em estado de confirmação (re-prompt)
            logger.info(f"ORCHESTRATOR | Confirmation Flow: Ambiguous message '{lower_message}'. Re-prompting.")
            response_payload["response"] = stored_confirmation_message
            response_payload["status"] = "awaiting_confirmation"
            if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
            await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
            return {"response_payload": response_payload}


    # --- 6. Lógica Principal de Inferência (SÓ SERÁ EXECUTADA SE NÃO ESTIVER EM CONFIRMAÇÃO PENDENTE) ---
    logger.debug(f"ORCHESTRATOR | No specific intent detected. Proceeding with main inference flow.")
    
    # 6.1. Processamento de Input para Gemini
    logger.debug(f"ORCHESTRATOR | Calling parse_incoming_input for message: '{user_message_for_processing[:50] if user_message_for_processing else '[no message]'}'")
    input_parser_results = await asyncio.to_thread(parse_incoming_input, user_message_for_processing, uploaded_file_data)
    user_prompt_parts = input_parser_results['prompt_parts_for_gemini']
    gemini_model_override = input_parser_results['gemini_model_override']
    gemini_final_model = gemini_vision_model if uploaded_file_data else gemini_text_model
    logger.debug(f"ORCHESTRATOR | Input parsed. Model selected: {gemini_final_model}")


    # 6.2. Detecção e Atualização de Configurações de Perfil (Direto)
    logger.debug(f"ORCHESTRATOR | Calling parse_and_update_profile_settings.")
    profile_settings_results = await parse_and_update_profile_settings(user_id, user_message_for_processing, user_profile_template_content)
    if profile_settings_results.get("profile_updated"):
        logger.debug(f"ORCHESTRATOR | Profile settings updated directly: {profile_settings_results.get('action_message')}")
        direct_action_message = profile_settings_results['action_message']
        user_profile = await get_user_profile_data(user_id, user_profile_template_content) # Recarrega o perfil após a atualização
        response_payload["response"] = direct_action_message
        response_payload["status"] = "success"
        response_payload["debug_info"] = {"intent_detected": "configuracao_perfil", "backend_action_result_status": "success"} # Renomeado para consistência
        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
        await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
        return {"response_payload": response_payload}

    # 6.3. Extração de Intenções CRUD/Rotina/Calendar pela LLM
    logger.debug(f"ORCHESTRATOR | Calling _extract_llm_action_intent.")
    # Renomeado de crud_intent_data para action_intent_data para refletir o escopo mais amplo
    action_intent_data = await _extract_llm_action_intent(user_id, user_message_for_processing, full_history, gemini_api_key, gemini_text_model, user_profile, all_routines) # Passa profile e rotinas
    intent_detected_in_orchestrator = action_intent_data.get("intent_detected", "conversa")
    logger.debug(f"ORCHESTRATOR | LLM intent extraction result: {intent_detected_in_orchestrator}")


    # 6.4. Processamento de Intenções (Task, Project, Routine ou Google Calendar)
    if intent_detected_in_orchestrator in ["task", "project", "routine", "google_calendar"]:
        logger.debug(f"ORCHESTRATOR | Detected LLM intent: {intent_detected_in_orchestrator}.")
        item_type = action_intent_data['intent_detected']
        action = action_intent_data['action']
        item_details = action_intent_data['item_details']
        llm_generated_confirmation_message = action_intent_data.get('confirmation_message') # Pode ser null

        # Usar item_details como base para o payload de confirmação, já que LLM pode adicionar campos específicos
        provisional_payload_data = item_details.copy() 

        # A data para aplicar a rotina ou sincronizar o calendário está no nível superior
        target_date_or_sync_start_date = action_intent_data.get('date') # Para routine/apply_routine
        sync_end_date = item_details.get('end_date') # Para google_calendar/sync_calendar

        provisional_payload = {
            "user_id": user_id,
            "item_type": item_type,
            "action": action,
            "item_id": item_details.get("id"), # ID do item para CRUD ou ID da rotina para apply_routine
            "data": provisional_payload_data, # Contém os detalhes específicos para cada tipo (task_description, routine_name, sync_details, etc.)
            "date": target_date_or_sync_start_date, # Usado para apply_routine ou sync_calendar como start_date
            "end_date": sync_end_date # Usado para sync_calendar
        }

        confirmation_message = llm_generated_confirmation_message # Prioriza a mensagem do LLM

        if item_type == 'task':
            task_description = item_details.get("description") # LLM deveria usar 'description' para tasks
            if not task_description: # Fallback para 'name' caso o LLM ainda use
                task_description = item_details.get("name") 
            
            task_date = provisional_payload_data.get("date") 
            task_time = provisional_payload_data.get("time")
            task_duration = provisional_payload_data.get("duration_minutes")

            # Validação para criação de tarefa (agora inclui hora)
            if action == 'create' and (not task_description or not task_date or not task_time):
                response_payload["response"] = "Para criar uma tarefa, preciso da descrição, data e hora. Por favor, seja mais específico."
                response_payload["status"] = "error"
                if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                return {"response_payload": response_payload}

            # Lógica de correção de data (se no passado, ajustar para ano corrente/próximo)
            if task_date:
                try:
                    parsed_date_obj = datetime.strptime(task_date, "%Y-%m-%d").date()
                    current_date_today = datetime.now(timezone.utc).date()
                    if parsed_date_obj < current_date_today:
                        test_current_year = parsed_date_obj.replace(year=current_date_today.year)
                        if test_current_year >= current_date_today:
                            task_date = test_current_year.isoformat()
                            logger.info(f"ORCHESTRATOR | Task date '{parsed_date_obj}' was in the past. Adjusted to {task_date} (current year).")
                        else:
                            test_next_year = parsed_date_obj.replace(year=current_date_today.year + 1)
                            task_date = test_next_year.isoformat()
                            logger.info(f"ORCHESTRATOR | Task date '{parsed_date_obj}' was in the past. Adjusted to {task_date} (next year).")
                except ValueError as ve:
                    logger.warning(f"ORCHESTRATOR | Task date '{task_date}' from LLM could not be parsed for year correction ({ve}). Using original from LLM as fallback.", exc_info=True)
            
            # Atualiza o provisional_payload_data que será salvo em data
            provisional_payload['data']['description'] = task_description
            provisional_payload['data']['date'] = task_date
            provisional_payload['data']['time'] = task_time
            provisional_payload['data']['duration_minutes'] = task_duration
            # Se a ação é 'complete', garantir que o status 'completed' seja true
            if action == 'complete':
                provisional_payload['action'] = 'update' # 'complete' é um update no crud_orchestrator
                provisional_payload['data']['completed'] = True

            # Mensagem de confirmação padrão se o LLM não forneceu uma específica
            if not confirmation_message:
                time_display = f" às {task_time}" if task_time else ""
                duration_display = f" por {task_duration} minutos" if task_duration else ""
                if action == 'create': confirmation_message = f"Confirma que deseja adicionar a tarefa '{task_description}' para {task_date}{time_display}{duration_display}?"
                elif action == 'complete': confirmation_message = f"Confirma que deseja marcar a tarefa '{task_description}' como concluída?"
                elif action == 'update': confirmation_message = f"Confirma que deseja atualizar a tarefa '{task_description}'?"
                elif action == 'delete': confirmation_message = f"Confirma que deseja excluir a tarefa '{task_description}'?"

        elif item_type == 'project':
            project_name = item_details.get("name")
            if action == 'create' and not project_name:
                response_payload["response"] = "Não consegui extrair o nome do projeto. Por favor, seja mais específico."
                response_payload["status"] = "error"
                if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                return {"response_payload": response_payload}
            
            # Mensagem de confirmação padrão se o LLM não forneceu uma específica
            if not confirmation_message:
                if action == 'create': confirmation_message = f"Confirma que deseja criar o projeto '{project_name}'?"
                elif action == 'update': confirmation_message = f"Confirma que deseja atualizar o projeto '{project_name}'?"
                elif action == 'delete': confirmation_message = f"Confirma que deseja excluir o projeto '{project_name}'?"
                elif action == 'complete': confirmation_message = f"Confirma que deseja marcar o projeto '{project_name}' como concluído?"
        
        elif item_type == 'routine':
            routine_name = item_details.get("routine_name")
            # Para 'apply_routine', a data vem no nível superior do JSON do LLM
            target_date_for_apply = action_intent_data.get('date') 
            
            if action == 'create':
                if not routine_name or not item_details.get('schedule'): # Rotina precisa de nome e schedule para criar
                    response_payload["response"] = "Para criar uma rotina, preciso do nome e dos itens/tarefas que a compõem."
                    response_payload["status"] = "error"
                    if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                    await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                    return {"response_payload": response_payload}
                
                # Garantir que cada item do schedule tenha um ID único
                for task_item in item_details.get('schedule', []):
                    if not task_item.get('id'):
                        task_item['id'] = str(uuid.uuid4())
                
                # A data do payload de confirmação para rotinas de criação não é aplicável
                provisional_payload['date'] = None
                # O ID da rotina é gerado no save_routine_template se não fornecido
                # provisional_payload['item_id'] = item_details.get('id') # Pode vir do LLM

                # Mensagem de confirmação padrão se o LLM não forneceu uma específica
                if not confirmation_message: confirmation_message = f"Confirma a criação da rotina '{routine_name}' com {len(item_details.get('schedule', []))} tarefas?"

            elif action == 'apply_routine':
                routine_id_from_llm = item_details.get("id") # ID direto da rotina
                routine_name_from_llm = item_details.get("routine_name") # Nome da rotina

                # Ajustar o provisional_payload para aplicar rotina
                provisional_payload['item_id'] = routine_id_from_llm # Passa o ID se veio
                provisional_payload['date'] = target_date_for_apply # Data alvo para apply_routine
                # O 'data' no provisional_payload para 'apply_routine' conterá apenas name/id para busca
                provisional_payload['data'] = {"name": routine_name_from_llm, "id": routine_id_from_llm} 

                if not target_date_for_apply:
                    response_payload["response"] = "Para aplicar uma rotina, preciso saber a data alvo (ex: 'para amanhã')."
                    response_payload["status"] = "error"
                    if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                    await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                    return {"response_payload": response_payload}

                # Se o LLM forneceu o nome da rotina mas não o ID, tentar encontrar pelo nome para a mensagem de confirmação
                if not routine_id_from_llm and routine_name_from_llm:
                    found_routine = next((r for r in all_routines if r.get('name', '').lower() == routine_name_from_llm.lower()), None)
                    if found_routine:
                        provisional_payload['item_id'] = found_routine['id'] # Atualiza o ID no payload
                        confirmation_message = f"Confirma a aplicação da rotina '{routine_name_from_llm}' para {target_date_for_apply}?"
                    else:
                        response_payload["response"] = f"Não encontrei nenhuma rotina chamada '{routine_name_from_llm}'. Por favor, verifique o nome ou crie a rotina primeiro."
                        response_payload["status"] = "error"
                        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                        await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                        return {"response_payload": response_payload}
                elif routine_id_from_llm and target_date_for_apply: # Já tem ID e data
                     confirmation_message = f"Confirma a aplicação da rotina para {target_date_for_apply}?"
                else: # Não tem nem nome nem ID
                    response_payload["response"] = "Não consegui identificar qual rotina aplicar. Por favor, seja mais específico."
                    response_payload["status"] = "error"
                    if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                    await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                    return {"response_payload": response_payload}
            
            elif action == 'delete': # Excluir uma rotina
                routine_id_to_delete = item_details.get("id")
                routine_name_to_delete = item_details.get("routine_name")
                
                if not routine_id_to_delete and not routine_name_to_delete:
                    response_payload["response"] = "Para excluir uma rotina, preciso do nome ou ID dela."
                    response_payload["status"] = "error"
                    if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                    await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                    return {"response_payload": response_payload}
                
                # Se só o nome foi dado, tentar encontrar o ID
                if not routine_id_to_delete and routine_name_to_delete:
                    found_routine = next((r for r in all_routines if r.get('name', '').lower() == routine_name_to_delete.lower()), None)
                    if found_routine:
                        provisional_payload['item_id'] = found_routine['id']
                        confirmation_message = f"Confirma a exclusão da rotina '{routine_name_to_delete}'?"
                    else:
                        response_payload["response"] = f"Não encontrei nenhuma rotina chamada '{routine_name_to_delete}' para excluir."
                        response_payload["status"] = "info"
                        if mode_debug_on: response_payload["debug_info"]["orchestrator_debug_log"].extend(debug_info_logs)
                        await save_interaction(user_id, user_input_for_saving, response_payload["response"], source_language, firestore_collection_interactions)
                        return {"response_payload": response_payload}
                else: # Tem o ID ou a LLM gerou a mensagem completa
                    confirmation_message = f"Confirma a exclusão da rotina '{routine_name_to_delete or routine_id_to_delete}'?"

        elif item_type == 'google_calendar':
            # As datas para sincronização estão no nível superior do JSON do LLM
            sync_start_date = action_intent_data.get('date') # Renomeado para evitar conflito
            sync_end_date = provisional_payload_data.get('end_date') # Mantém como estava

            # O provisional_payload já está com os dados do LLM, incluindo start_date/end_date em 'data'
            # Mas o 'date' do LLM JSON virá para 'provisional_payload['date']'
            # Reorganizar aqui para garantir que 'data' tenha 'start_date' e 'end_date'
            if sync_start_date: # A data do LLM (action_intent_data.get('date')) é o 'start_date' para sync
                provisional_payload['data']['start_date'] = sync_start_date
            if sync_end_date:
                provisional_payload['data']['end_date'] = sync_end_date

            if action == 'connect_calendar':
                current_creds = await google_calendar_auth_manager.get_credentials(user_id)
                if current_creds:
                    # Se já conectado, a intenção de "conectar" se transforma em "sincronizar"
                    provisional_payload = {
                        "user_id": user_id,
                        "item_type": "google_calendar",
                        "action": "sync_calendar", # Muda a ação para sync
                        "data": {"start_date": datetime.now(timezone.utc).isoformat(), "end_date": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()},
                        "date": datetime.now(timezone.utc).isoformat(), # start_date do sync
                        "end_date": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat() # end_date do sync
                    }
                    confirmation_message = "Você já está conectado ao Google Calendar. Deseja sincronizar seus eventos para os próximos 7 dias?"
                else:
                    confirmation_message = "Você gostaria de conectar a EIXA ao seu Google Calendar para que eu possa ver seus eventos e ajudá-lo melhor?"
            
            elif action == 'sync_calendar':
                # Define um período padrão se o LLM não especificar (ex: próxima semana)
                if not sync_start_date or not sync_end_date:
                    start_date_obj = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                    end_date_obj = start_date_obj + timedelta(days=7)
                    provisional_payload['data']['start_date'] = start_date_obj.isoformat()
                    provisional_payload['data']['end_date'] = end_date_obj.isoformat()
                    provisional_payload['date'] = start_date_obj.isoformat() # Atualiza o nível superior também
                    provisional_payload['end_date'] = end_date_obj.isoformat() # Atualiza o nível superior também
                    confirmation_message = f"Confirma a sincronização dos eventos do seu Google Calendar para os próximos 7 dias (de {start_date_obj.strftime('%d/%m')} a {end_date_obj.strftime('%d/%m')})?"
                else:
                    confirmation_message = f"Confirma a sincronização dos eventos do seu Google Calendar de {sync_start_date} a {sync_end_date}?"
                
                # Verifica se há credenciais antes de prosseguir para a confirmação.
                # Se não houver, muda a ação para "connect_calendar" e pede para conectar primeiro.
                creds = await google_calendar_auth_manager.get_credentials(user_id)
                if not creds:
                    response_payload["response"] = "Para sincronizar seus eventos do Google Calendar, primeiro preciso que você conecte sua conta Google à EIXA. Gostaria de fazer isso agora?"
                    response_payload["status"] = "info"
                    # Mudar o payload para pedir confirmação de "connect_calendar"
                    provisional_payload = {"user_id": user_id, "item_type": "google_calendar", "action": "connect_calendar", "data": {}}
                    confirmation_message = "Para sincronizar, preciso que você conecte seu Google Calendar. Confirma que quer conectar agora?"

            elif action == 'disconnect_calendar':
                confirmation_message = "Confirma que deseja desconectar sua conta Google da EIXA? Não poderei mais ver seus eventos do Google Calendar."

        # Salva o estado de confirmação para todas as ações
        await set_confirmation_state(
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
        return {"response_payload": response_payload}


    # --- 7. Lógica de Conversação Genérica com LLM (Se nenhuma intenção específica foi tratada) ---
    logger.debug(f"ORCHESTRATOR | No specific intent detected. Proceeding with main inference flow.")
    
    conversation_history = []
    recent_history_for_llm = full_history[-5:]
    for turn in recent_history_for_llm:
        if turn.get("input"): conversation_history.append({"role": "user", "parts": [{"text": turn.get("input")}]})
        if turn.get("output"): conversation_history.append({"role": "model", "parts": [{"text": turn.get("output")}]})
    debug_info_logs.append(f"History prepared with {len(recent_history_for_llm)} turns for LLM context.")

    current_datetime_utc = datetime.now(timezone.utc)
    day_names_pt = {0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira", 3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo"}
    current_date_iso_formatted = current_datetime_utc.strftime('%Y-%m-%d')
    current_time_formatted = current_datetime_utc.strftime('%H:%M')

    # CONTEXTO TEMPORAL MELHORADO
    contexto_temporal = f"""--- CONTEXTO TEMPORAL ATUAL ---
    A data atual é {current_date_iso_formatted} ({day_names_pt[current_datetime_utc.weekday()]}). O horário atual é {current_time_formatted}. O ano atual é {current_datetime_utc.year}.
    O fuso horário do usuário é {user_profile.get('timezone', DEFAULT_TIMEZONE)}.
    --- FIM DO CONTEXTO TEMPORAL ---\n\n"""
    debug_info_logs.append("Temporal context generated for LLM.")

    # Memória Vetorial (Contextualização de Longo prazo)
    if user_message_for_processing and gcp_project_id and region:
        logger.debug(f"ORCHESTRATOR | Attempting to generate embedding for user query.")
        user_query_embedding = await get_embedding(user_message_for_processing, gcp_project_id, region, model_name=EMBEDDING_MODEL_NAME)
        if user_query_embedding:
            relevant_memories = await get_relevant_memories(user_id, user_query_embedding, n_results=5)
            if relevant_memories:
                context_string = "\n".join(["--- CONTEXTO DE MEMÓRIAS RELEVANTES DE LONGO PRAZO:"] + [f"- {mem['content']}" for mem in relevant_memories])
                logger.info(f"ORCHESTRATOR | Adding {len(relevant_memories)} relevant memories to LLM context for user '{user_id}'.")
                conversation_history.insert(0, {"role": "user", "parts": [{"text": context_string}]})
        else:
            logger.warning(f"ORCHESTRATOR | Could not generate embedding for user message. Skipping vector memory retrieval.", exc_info=True)
            debug_info_logs.append("Warning: Embedding generation failed, vector memory not used.")

    conversation_history.append({"role": "user", "parts": user_prompt_parts})

    # Constrói o contexto crítico de tarefas, projetos e AGORA ROTINAS
    contexto_critico = "--- TAREFAS PENDENTES, PROJETOS ATIVOS E ROTINAS SALVAS DO USUÁRIO ---\n"
    logger.debug(f"ORCHESTRATOR | Fetching all daily tasks, projects and routines for critical context.")
    current_tasks = await get_all_daily_tasks(user_id)
    flat_current_tasks = []
    for date_key, day_data in current_tasks.items():
        for task_data in day_data.get('tasks', []):
            status = 'Concluída' if task_data.get('completed', False) else 'Pendente'
            # INCLUINDO HORA E DURAÇÃO NAS TAREFAS DO CONTEXTO
            time_info = f" às {task_data.get('time', 'N/A')}" if task_data.get('time') else ""
            duration_info = f" por {task_data.get('duration_minutes', 'N/A')} minutos" if task_data.get('duration_minutes') else ""
            
            # Adicionado: Informação sobre a origem da tarefa (Rotina, Google Calendar, etc.)
            origin_info = ""
            if task_data.get('origin') == 'routine':
                origin_info = " (Origem: Rotina)"
            elif task_data.get('origin') == 'google_calendar':
                origin_info = " (Origem: Google Calendar)"
            
            # Adicionado ID e Data de Criação (se existirem)
            task_id_info = f" (ID: {task_data.get('id', 'N/A')})" if task_data.get('id') else ""
            created_at_info = f" (Adicionada em: {task_data.get('created_at', 'N/A')})" if task_data.get('created_at') else ""


            flat_current_tasks.append(f"- {task_data.get('description', 'N/A')} (Data: {date_key}{time_info}{duration_info}, Status: {status}{origin_info}{task_id_info}{created_at_info})")

    current_projects = await get_all_projects(user_id)
    formatted_projects = []
    for project in current_projects:
        status = project.get('status', 'N/A')
        deadline = project.get('deadline', 'N/A')
        formatted_projects.append(f"- {project.get('name', 'N/A')} (Status: {status}, Prazo: {deadline})")
    
    formatted_routines = []
    if all_routines:
        for routine in all_routines:
            routine_name = routine.get('name', 'Rotina sem nome')
            routine_id = routine.get('id', 'N/A')
            routine_desc = routine.get('description', 'N/A')
            routine_days = ", ".join(routine.get('applies_to_days', [])) if routine.get('applies_to_days') else 'Todos os dias'
            schedule_summary = []
            for item in routine.get('schedule', []):
                # Inclui ID e Created_at dos itens da rotina no contexto
                item_id = item.get('id', 'N/A')
                item_time = item.get('time', 'N/A')
                item_desc = item.get('description', 'N/A')
                item_duration = item.get('duration_minutes', 'N/A')
                item_created_at = item.get('created_at', 'N/A')
                schedule_summary.append(f"({item_id}) {item_time} - {item_desc} ({item_duration}min, Criada em: {item_created_at})")
            
            formatted_routines.append(f"- Rotina '{routine_name}' (ID: {routine_id}, Descrição: {routine_desc}, Aplica-se a: {routine_days}). Itens: {'; '.join(schedule_summary)}")


    if flat_current_tasks:
        contexto_critico += "Tarefas Pendentes:\n" + "\n".join(flat_current_tasks) + "\n"
    else: contexto_critico += "Nenhuma tarefa pendente registrada.\n"
    
    if formatted_projects:
        contexto_critico += "\nProjetos Ativos:\n" + "\n".join(formatted_projects) + "\n"
    else: contexto_critico += "\nNenhum projeto ativo registrado.\n"

    if formatted_routines:
        contexto_critico += "\nRotinas Salvas:\n" + "\n".join(formatted_routines) + "\n"
    else: contexto_critico += "\nNenhuma rotina salva.\n"

    # NOVO: Adiciona status de conexão com Google Calendar
    google_calendar_status = "Não Conectado"
    # Você já tem a instância de google_calendar_auth_manager.
    # A chamada get_credentials retorna None se não há credenciais ou elas são inválidas.
    if await google_calendar_auth_manager.get_credentials(user_id):
        google_calendar_status = "Conectado"
    contexto_critico += f"\nStatus do Google Calendar: {google_calendar_status}\n"
    
    contexto_critico += "--- FIM DO CONTEXTO CRÍTICO ---\n\n"
    debug_info_logs.append("Critical context generated for LLM.")


    # Constrói o contexto de perfil (detalhado) - SEM ALTERAÇÕES AQUI. Manter o que já tinha
    base_persona_with_name = base_eixa_persona_template_text.replace("{{user_display_name}}", user_display_name)
    contexto_perfil_str = f"--- CONTEXTO DO PERFIL DO USUÁRIO ({user_display_name}):\n"
    profile_summary_parts = []
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
        project_names_from_profile = [p.get('name', 'N/A') for p in user_profile['current_projects'] if isinstance(p, dict)]
        if project_names_from_profile: profile_summary_parts.append(f"   - Projetos Atuais (do perfil): {', '.join(project_names_from_profile)}")

    if user_profile.get('goals', {}) and isinstance(user_profile['goals'], dict):
        for term_type in ['long_term', 'medium_term', 'short_term']:
            if user_profile['goals'].get(term_type) and isinstance(user_profile['goals'][term_type], list) and user_profile['goals'][term_type]:
                goals_text = [g.get('value', 'N/A') for g in user_profile['goals'][term_type] if isinstance(g, dict) and g.get('value')]
                if goals_text: profile_summary_parts.append(f"   - Metas de {'Longo' if term_type == 'long_term' else 'Médio' if term_type == 'medium_term' else 'Curto'} Prazo: {', '.join(goals_text)}")

    if user_profile.get('eixa_interaction_preferences', {}).get('expected_eixa_actions') and isinstance(user_profile['eixa_interaction_preferences']['expected_eixa_actions'], list) and user_profile['eixa_interaction_preferences']['expected_eixa_actions']:
        actions_text = user_profile['eixa_interaction_preferences']['expected_eixa_actions']
        profile_summary_parts.append(f"   - Ações Esperadas da EIXA: {', '.join(actions_text)}")
    
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
            if alerts_rem.get('meal_times') and isinstance(alerts_rem['meal_times'], list) and alerts_rem['meal_times']: daily_routine_list.append(f"Alerta Refeições: {', '.join(alerts_rem['meal_times'])}")
            if alerts_rem.get('medication_reminders') and isinstance(alerts_rem['medication_reminders'], list) and alerts_rem['medication_reminders']: daily_routine_list.append(f"Alerta Medicação: {', '.join(alerts_rem['medication_reminders'])}")
            if alerts_rem.get('overwhelm_triggers') and isinstance(alerts_rem['overwhelm_triggers'], list) and alerts_rem['overwhelm_triggers']: daily_routine_list.append(f"Gatilhos Sobrecarga: {', '.join(alerts_rem['overwhelm_triggers'])}")
            if alerts_rem.get('burnout_indicators') and isinstance(alerts_rem['burnout_indicators'], list) and alerts_rem['burnout_indicators']: daily_routine_list.append(f"Indicadores Burnout: {', '.join(alerts_rem['burnout_indicators'])}")
        
        if daily_routine_list: profile_summary_parts.append(f"   - Elementos da Rotina Diária: {'; '.join(daily_routine_list)}")
    
    if user_profile.get('data_usage_consent') is not None:
        profile_summary_parts.append(f"   - Consentimento de Uso de Dados: {'Concedido' if user_profile['data_usage_consent'] else 'Não Concedido'}")
    
    if user_profile.get('locale'): profile_summary_parts.append(f"   - Localidade: {user_profile['locale']}")
    if user_profile.get('timezone'): profile_summary_parts.append(f"   - Fuso Horário: {user_profile['timezone']}")
    if user_profile.get('age_range'): profile_summary_parts.append(f"   - Faixa Etária: {user_profile['age_range']}")
    if user_profile.get('gender_identity'): profile_summary_parts.append(f"   - Gênero: {user_profile['gender_identity']}")
    if user_profile.get('education_level'): profile_summary_parts.append(f"   - Nível Educacional: {user_profile['education_level']}")

    contexto_perfil_str += "\n".join(profile_summary_parts) if profile_summary_parts else "   Nenhum dado de perfil detalhado disponível.\n"
    contexto_perfil_str += "--- FIM DO CONTEXTO DE PERFIL ---\n\n"

    final_system_instruction = contexto_temporal + contexto_critico + contexto_perfil_str + base_persona_with_name

    # Chamada LLM genérica
    logger.debug(f"ORCHESTRATOR | Calling Gemini API for generic response. Model: {gemini_final_model}")
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
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', final_ai_response, re.DOTALL)
        if json_match:
            try:
                profile_update_json_str = json_match.group(1)
                profile_update_data = json.loads(profile_update_json_str)
                profile_update_json = profile_update_data.get('profile_update')

                pre_json_text = final_ai_response[:json_match.start()].strip()
                post_json_text = final_ai_response[json_match.end():].strip()
                
                final_ai_response = (pre_json_text + "\n\n" + post_json_text).strip()
                final_ai_response = final_ai_response.replace('\n\n\n', '\n\n')
                
                logger.info(f"ORCHESTRATOR | Detected profile_update JSON from LLM for user '{user_id}'.")
            except json.JSONDecodeError as e:
                logger.warning(f"ORCHESTRATOR | Failed to parse profile_update JSON from LLM: {e}. Raw JSON: {json_match.group(1)[:100]}...", exc_info=True)
            except AttributeError as e:
                logger.warning(f"ORCHESTRATOR | Profile update JSON missing 'profile_update' key or has unexpected structure: {e}. Raw data: {profile_update_data}", exc_info=True)

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
        logger.debug(f"ORCHESTRATOR | Attempting to generate embedding for interaction.")
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
    if intent_detected_in_orchestrator == "routine" and action == "create":
        emotional_tags.append("rotina_criada")
    if intent_detected_in_orchestrator == "routine" and action == "apply_routine":
        emotional_tags.append("rotina_aplicada")
    if intent_detected_in_orchestrator == "google_calendar" and (action == "sync_calendar" or action == "connect_calendar"):
        emotional_tags.append("google_calendar_integrado")

    sabotage_patterns_detected = await get_sabotage_patterns(user_id, 20, user_profile)
    logger.debug(f"ORCHESTRATOR | Raw sabotage patterns detected: {sabotage_patterns_detected}")

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

    filtered_patterns = {p: f for p, f in sabotage_patterns_detected.items() if f >= 2}
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

    return {"response_payload": response_payload}