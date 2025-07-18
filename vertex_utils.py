# vertex_utils.py
import os
import httpx
import json
import logging

# Importa as configurações do config.py
from config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_TEMPERATURE, EMBEDDING_MODEL_NAME

# Importação corrigida para a versão estável da biblioteca Vertex AI
from vertexai.language_models import TextEmbeddingModel

logger = logging.getLogger(__name__)

async def call_gemini_api(
    api_key: str,
    model_name: str,
    conversation_history: list[dict],
    system_instruction: str = None,
    # **CORREÇÃO AQUI**: Usando os valores padrão do config.py
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    debug_mode: bool = False
) -> str | None:
    """
    Chama o modelo Gemini da Generative Language API via REST de forma assíncrona.
    Inclui tratamento para respostas truncadas e configurações de segurança.
    """
    api_endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

    headers = {"Content-Type": "application/json"}

    payload = {
        "contents": conversation_history,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "topP": 0.95,
            "topK": 40
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "HARM_BLOCK_THRESHOLD_UNSPECIFIED"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "HARM_BLOCK_THRESHOLD_UNSPECIFIED"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "HARM_BLOCK_THRESHOLD_UNSPECIFIED"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "HARM_BLOCK_THRESHOLD_UNSPECIFIED"},
        ],
    }

    if system_instruction:
        payload["system_instruction"] = {
            "parts": [{"text": system_instruction}]
        }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(api_endpoint, headers=headers, json=payload, params={"key": api_key})
            response.raise_for_status() 
            response_json = response.json()

            if debug_mode:
                logger.debug(f"Full Gemini API response JSON: {json.dumps(response_json, indent=2)}")

            # --- CORREÇÃO: Tratamento robusto para extrair texto dos candidatos ---
            generated_text = None # Inicializa generated_text como None por padrão
            
            if response_json.get('candidates'):
                first_candidate = response_json['candidates'][0]
                finish_reason = first_candidate.get('finishReason', 'UNKNOWN')
                
                # Garante que 'content' e 'parts' existem e são acessíveis
                content = first_candidate.get('content', {})
                parts = content.get('parts', []) # Inicializa parts como uma lista vazia se não existir
                
                # Acessa o texto, mas com segurança caso parts esteja vazia ou o texto não exista
                if parts and len(parts) > 0:
                    generated_text = parts[0].get('text')
                else:
                    logger.warning(f"Gemini API response has candidates but no parts or no text in first part. Finish reason: {finish_reason}. Response: {json.dumps(response_json, indent=2)}")

                if generated_text is not None and generated_text != "": # Verifica se generated_text é string não vazia
                    logger.info(f"Gemini response text extracted successfully (first 100 chars): '{generated_text[:100]}...'")
                    if finish_reason != 'STOP':
                        logger.warning(f"Gemini response finished with reason: {finish_reason}. Appending truncation warning.")
                        generated_text += "\n\n[⚠️ AVISO: A resposta pode estar incompleta, pois o modelo atingiu um limite.]"
                    return generated_text
                else: # Se generated_text é None ou vazio após a tentativa de extração
                    logger.warning(f"Gemini API response has candidates but generated_text is empty or None. Finish reason: {finish_reason}. This indicates the model likely finished due to MAX_TOKENS or generation constraint. Full Response: {json.dumps(response_json, indent=2)}")
                    return None # Retornar None para o orquestrador lidar com a resposta vazia

            else: # Se não há 'candidates' (ex: bloqueio de segurança ou erro na resposta da API)
                safety_ratings = response_json.get('promptFeedback', {}).get('safetyRatings', [])
                if safety_ratings:
                    logger.warning(f"Gemini API response blocked due to safety. Prompt Feedback: {json.dumps(safety_ratings, indent=2)}")
                    return "Sua solicitação foi bloqueada por razões de segurança. Por favor, reformule sua mensagem."
                
                logger.warning(f"Gemini API response without valid candidates. Response: {json.dumps(response_json, indent=2)}")
                return None

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP Error calling Gemini API: {e.response.status_code} - {e.response.text}", exc_info=True)
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from Gemini API response: {e}. Response text: {e.response.text if hasattr(e, 'response') else 'No response text'}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error calling Gemini API: {e}", exc_info=True)
        return None

async def count_gemini_tokens(api_key: str, model_name: str, parts_to_count: list[dict], debug_mode: bool = False) -> int:
    """
    Conta o número de tokens em uma lista de partes de um prompt usando a API do Gemini.
    Retorna a contagem de tokens ou uma estimativa em caso de falha da API.
    """
    api_endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:countTokens"
    payload = {"contents": [{"role": "user", "parts": parts_to_count}]}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(api_endpoint, json=payload, params={"key": api_key})
            response.raise_for_status()
            response_json = response.json()
            
            if debug_mode:
                logger.debug(f"Gemini token count response: {response_json}")
                
            return response_json.get("totalTokens", 0)
    except Exception as e:
        logger.warning(f"Falha ao contar tokens via API: {e}. Usando contagem de caracteres como fallback (aproximado).", exc_info=True)
        total_chars = sum(len(p.get("text", "")) for p in parts_to_count if "text" in p)
        return int(total_chars / 4)