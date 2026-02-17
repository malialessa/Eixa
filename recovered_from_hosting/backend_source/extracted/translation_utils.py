import logging
from google.cloud import translate_v2 as translate
import os
import asyncio

logger = logging.getLogger(__name__)

# Instancia o cliente de tradução (uma única vez por instância da função/processo)
_translate_client = None

def _get_translate_client():
    global _translate_client
    if _translate_client is None:
        try:
            _translate_client = translate.Client()
            logger.info("Google Cloud Translate client initialized successfully.")
        except Exception as e:
            logger.critical(f"Failed to initialize Google Cloud Translate client: {e}", exc_info=True)
            raise
    return _translate_client

async def detect_language(text: str) -> str:
    """
    Detecta o idioma de um dado texto usando a Google Cloud Translation API.
    Retorna o código do idioma (ex: 'en', 'pt') ou 'pt' como fallback.
    """
    if not text or not text.strip():
        return 'pt' # Fallback para português se o texto for vazio ou só espaços

    try:
        client = _get_translate_client()
        
        detection_result = await asyncio.to_thread(client.detect_language, text)

        detected_lang = detection_result['language']
        confidence = detection_result['confidence']

        logger.info(f"Idioma detectado para texto (primeiros 20 caracteres): '{text[:20]}...' é '{detected_lang}' com confiança {confidence:.2f}.")

        # Lista de idiomas que você realmente espera que o usuário fale
        expected_languages_base = ['pt', 'en', 'es'] # Usar a base do idioma

        # PRIORIDADE 1: Se detectou um idioma base esperado com alta confiança, use-o.
        if detected_lang.split('-')[0] in expected_languages_base and confidence >= 0.90: # Confiança um pouco mais flexível aqui
             return detected_lang.split('-')[0] # Retorna a parte base do idioma (ex: 'pt' de 'pt-PT')

        # PRIORIDADE 2: Se detectou um idioma não esperado com ALTA confiança (quase certeza), pode ser válido.
        # No entanto, para o seu caso de 'pt-PT', queremos normalizar.
        # Se for pt-PT e queremos tratar como pt, a normalização abaixo vai lidar.

        # PRIORIDADE 3: Casos de baixa confiança ou texto curto/ambíguo.
        # Se a confiança é baixa OU o texto é muito curto E o idioma detectado não é 'pt', force para 'pt'.
        if confidence < 0.95 or len(text.split()) < 3: 
            if detected_lang.split('-')[0] != 'pt': # Só força se não for uma variação de português
                logger.warning(f"Baixa confiança na detecção de idioma ({confidence:.2f}) ou texto curto para '{text[:20]}...'. Forçando para 'pt' como fallback.")
                return 'pt'
            else:
                # Se for uma variação de português com baixa confiança ou texto curto, ainda normaliza para 'pt'
                logger.warning(f"Idioma detectado '{detected_lang}' com baixa confiança ou texto curto para '{text[:20]}...'. Normalizando para 'pt'.")
                return 'pt'

        # FALLBACK FINAL: Se nada acima pegou, apenas retorna o idioma detectado (normalizado se for uma variação)
        return detected_lang.split('-')[0] # Garante que sempre retorna a base (ex: 'pt' em vez de 'pt-PT')

    except Exception as e:
        logger.error(f"Erro ao detectar idioma: {e}. Retornando 'pt' como fallback.", exc_info=True)
        return 'pt' # Fallback em caso de erro

async def translate_text(text: str, target_language: str, source_language: str = None) -> str | None:
    """
    Traduz um texto para o idioma alvo usando a Google Cloud Translation API.
    Retorna o texto traduzido ou None em caso de falha.
    """
    if not text or not text.strip():
        return "" # Retorna string vazia se não houver texto para traduzir

    # NORMALIZAÇÃO PARA VERIFICAÇÃO DE IDIOMAS IGUAIS/SIMILARES
    # Isso resolve o erro 'Bad language pair: pt-PT|pt'
    normalized_source = source_language.split('-')[0] if source_language else None
    normalized_target = target_language.split('-')[0]

    # Se a base do idioma de origem é igual à base do idioma de destino, pular tradução
    if normalized_source == normalized_target:
        logger.info(f"Translation skipped: Normalized source language '{normalized_source}' is same as normalized target language '{normalized_target}'. Returning original text.")
        return text

    try:
        client = _get_translate_client()
        
        # A tradução é uma operação síncrona
        if source_language:
            result = await asyncio.to_thread(client.translate, text, target_language=target_language, source_language=source_language)
        else:
            result = await asyncio.to_thread(client.translate, text, target_language=target_language) # API detectará source automaticamente

        translated_text = result['translatedText']
        logger.info(f"Texto traduzido de '{result.get('detectedSourceLanguage', source_language)}' para '{target_language}'. Original: '{text[:50]}...', Traduzido: '{translated_text[:50]}...'.")
        return translated_text
    except Exception as e:
        logger.error(f"Erro ao traduzir texto para '{target_language}': {e}", exc_info=True)
        return None # Retorna None em caso de falha na tradução