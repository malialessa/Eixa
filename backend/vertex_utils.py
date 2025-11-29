import os
import httpx
import json
import logging
import asyncio
from metrics_utils import measure_async, record_latency

from config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_TEMPERATURE, EMBEDDING_MODEL_NAME
from vertexai.language_models import TextEmbeddingModel
import vertexai
from vertexai.generative_models import GenerativeModel

logger = logging.getLogger(__name__)

@measure_async("vertex.call_gemini_api")
async def call_gemini_api(
    api_key: str,
    model_name: str,
    conversation_history: list[dict],
    system_instruction: str = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    debug_mode: bool = False,
    project_id: str | None = None,
    region: str | None = None
) -> str | None:
    """Chama Gemini.
    Se api_key fornecida => usa REST generativelanguage.
    Caso contrário => usa Vertex AI SDK (ADC) para evitar dependência de API key.
    """
    if not api_key:
        # Vertex AI SDK path
        try:
            if project_id and region:
                vertexai.init(project=project_id, location=region)
            model = GenerativeModel(model_name, system_instruction=system_instruction)
            # Converter conversation_history (formato parts) para lista de Content simples
            messages = []
            for turn in conversation_history:
                role = turn.get("role")
                parts = turn.get("parts", [])
                text_parts = []
                for p in parts:
                    if isinstance(p, dict) and 'text' in p:
                        text_parts.append(p['text'])
                combined = "\n".join(text_parts)
                if combined:
                    messages.append({"role": role, "content": combined})

            # generate_content aceita lista de strings ou Content; simplificamos para lista concatenada
            # Juntar sequência user/model alternada em um único prompt preservando ordem
            prompt_segments = []
            for m in messages:
                prefix = "User:" if m["role"] == "user" else "Model:"
                prompt_segments.append(f"{prefix} {m['content']}")
            final_prompt = "\n".join(prompt_segments)

            response = await asyncio.to_thread(
                model.generate_content,
                [final_prompt],
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                    "top_p": 0.95,
                    "top_k": 40,
                }
            )
            text = getattr(response, 'text', None)
            if debug_mode and text:
                logger.debug(f"Vertex Gemini response (first 500 chars): {text[:500]}")
            # Métrica adicional de sucesso lógico (texto retornado)
            record_latency("vertex.gemini.sdk.result", 0.0, bool(text))
            return text
        except Exception as e:
            logger.error(f"Vertex AI SDK call failed (fallback to REST not possible without api_key): {e}", exc_info=True)
            return None

    # REST path (api_key provided)
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
        payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(api_endpoint, headers=headers, json=payload, params={"key": api_key})
            response.raise_for_status()
            response_json = response.json()
            if debug_mode:
                logger.debug(f"Full Gemini API response JSON: {json.dumps(response_json, indent=2)}")
            generated_text = None
            if response_json.get('candidates'):
                first_candidate = response_json['candidates'][0]
                finish_reason = first_candidate.get('finishReason', 'UNKNOWN')
                content = first_candidate.get('content', {})
                parts = content.get('parts', [])
                if parts:
                    generated_text = parts[0].get('text')
                else:
                    logger.warning(f"Gemini API response has candidates but no parts or text. Reason: {finish_reason}")
                if generated_text:
                    if finish_reason != 'STOP':
                        generated_text += "\n\n[⚠️ AVISO: A resposta pode estar incompleta, limite atingido.]"
                    record_latency("vertex.gemini.rest.result", 0.0, True)
                    return generated_text
                record_latency("vertex.gemini.rest.result", 0.0, False)
                return None
            else:
                safety_ratings = response_json.get('promptFeedback', {}).get('safetyRatings', [])
                if safety_ratings:
                    logger.warning(f"Gemini API blocked due to safety: {json.dumps(safety_ratings, indent=2)}")
                    return "Sua solicitação foi bloqueada por razões de segurança. Reformule a mensagem."
                logger.warning("Gemini API response sem candidatos válidos.")
                record_latency("vertex.gemini.rest.result", 0.0, False)
                return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP Error calling Gemini API: {e.response.status_code} - {e.response.text}", exc_info=True)
        record_latency("vertex.gemini.rest.result", 0.0, False)
        return None
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error da resposta Gemini: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Erro inesperado na chamada REST Gemini: {e}", exc_info=True)
        return None

@measure_async("vertex.count_gemini_tokens")
async def count_gemini_tokens(api_key: str, model_name: str, parts_to_count: list[dict], debug_mode: bool = False) -> int:
    api_endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:countTokens"
    payload = {"contents": [{"role": "user", "parts": parts_to_count}]}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(api_endpoint, json=payload, params={"key": api_key})
            response.raise_for_status()
            response_json = response.json()
            if debug_mode: logger.debug(f"Gemini token count response: {response_json}")
            return response_json.get("totalTokens", 0)
    except Exception as e:
        logger.warning(f"Falha ao contar tokens via API: {e}. Usando contagem de caracteres como fallback (aproximado).", exc_info=True)
        total_chars = sum(len(p.get("text", "")) for p in parts_to_count if "text" in p)
        return int(total_chars / 4)