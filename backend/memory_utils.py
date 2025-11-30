# --- START OF FILE memory_utils.py ---

import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from google.cloud import firestore

from firestore_client_singleton import _initialize_firestore_client_instance
from collections_manager import get_top_level_collection
import eixa_data # Importação necessária para get_user_history, como já estava no seu código.

logger = logging.getLogger(__name__)

async def add_emotional_memory(user_id: str, content: str, tags: list[str]) -> None:
    if not tags:
        logger.debug(f"Nenhuma tag emocional fornecida para o conteúdo, memória não salva para o usuário '{user_id}'.")
        return

    client_timestamp = datetime.now(timezone.utc)
    doc_id = f"{user_id}_{client_timestamp.isoformat().replace(':', '-').replace('.', '_')}"

    memory_data = {
        "user_id": user_id,
        "timestamp": firestore.SERVER_TIMESTAMP,
        "content": content,
        "tags": tags
    }

    try:
        memories_collection = get_top_level_collection('memories')
        db = _initialize_firestore_client_instance()
        await asyncio.to_thread(db.collection(memories_collection.id).document(doc_id).set, memory_data)
        logger.info(f"Memória emocional com tags {tags} salva para o usuário '{user_id}'. Doc ID: {doc_id}")
    except Exception as e:
        logger.error(f"Erro ao salvar memória emocional para o usuário '{user_id}': {e}", exc_info=True)

async def get_emotional_memories(user_id: str, n: int = 5) -> list[dict]:
    db = _initialize_firestore_client_instance()
    memories = []

    try:
        memories_ref = get_top_level_collection('memories')
        query = memories_ref.where("user_id", "==", user_id) \
            .order_by("timestamp", direction=firestore.Query.DESCENDING) \
            .limit(n)

        docs = await asyncio.to_thread(lambda: list(query.stream()))

        for doc in docs:
            memory = doc.to_dict()
            memory['id'] = doc.id
            memories.append(memory)

        logger.info(f"Recuperadas {len(memories)} memórias emocionais para o usuário '{user_id}'.")
        return memories
    except Exception as e:
        logger.error(f"Erro ao recuperar memórias emocionais para o usuário '{user_id}': {e}", exc_info=True)
        return []

def detect_sabotage_patterns(texts: list[str], user_profile: Dict[str, Any]) -> dict:
    sabotage_phrases = [
        "deixar para depois", "amanhã eu faço", "não consigo", "é muito difícil",
        "procrastinar", "estou adiando", "não vou dar conta", "desisto",
        "sem energia", "cansado demais", "sem vontade", "perdido", "sobrecarregado",
        "bloqueado", "não sei por onde começar"
    ]

    if user_profile and user_profile.get('psychological_profile'):
        psych_profile = user_profile['psychological_profile']

        if psych_profile.get('historical_behavioral_patterns'):
            for pattern_phrase in psych_profile['historical_behavioral_patterns']:
                sabotage_phrases.append(pattern_phrase.lower().replace("_", " "))

        if psych_profile.get('diagnoses_and_conditions'):
            for condition_phrase in psych_profile['diagnoses_and_conditions']:
                sabotage_phrases.append(condition_phrase.lower().replace("_", " "))

        if psych_profile.get('coping_mechanisms'):
            for coping_mechanism in psych_profile['coping_mechanisms']:
                sabotage_phrases.append(coping_mechanism.lower().replace("_", " "))

    patterns_found = {}
    for text in texts:
        lower_text = text.lower()
        for phrase in sabotage_phrases:
            if phrase in lower_text:
                patterns_found[phrase] = patterns_found.get(phrase, 0) + 1

    return patterns_found

async def get_sabotage_patterns(user_id: str, n: int = 20, user_profile: Dict[str, Any] = None) -> dict:
    logger.debug(f"Analisando últimas {n} interações para padrões de sabotagem do usuário '{user_id}'.")

    history = await eixa_data.get_user_history(user_id, 'interactions', n) # Alterei para usar eixa_data.get_user_history

    if not history:
        logger.debug(f"Nenhum histórico de interação encontrado para o usuário '{user_id}'.")
        return {}

    user_inputs = [item.get('input', '') for item in history if item.get('input')]

    detected = detect_sabotage_patterns(user_inputs, user_profile)

    if detected:
        logger.info(f"Padrões de sabotagem detectados para o usuário '{user_id}': {detected}")

    return detected

async def save_mood_log(user_id: str, mood_score: int, note: str = "") -> None:
    """
    Salva um registro de humor no Firestore.
    
    Args:
        user_id: ID do usuário
        mood_score: Pontuação do humor (1-10)
        note: Nota opcional sobre o humor
    """
    if not (1 <= mood_score <= 10):
        logger.warning(f"Mood score inválido ({mood_score}) para usuário '{user_id}'. Deve estar entre 1-10.")
        return

    client_timestamp = datetime.now(timezone.utc)
    doc_id = f"{user_id}_{client_timestamp.isoformat().replace(':', '-').replace('.', '_')}"

    mood_data = {
        "user_id": user_id,
        "timestamp": firestore.SERVER_TIMESTAMP,
        "mood_score": mood_score,
        "note": note,
        "created_at": client_timestamp.isoformat()
    }

    try:
        mood_logs_collection = get_top_level_collection('mood_logs')
        db = _initialize_firestore_client_instance()
        await asyncio.to_thread(db.collection(mood_logs_collection.id).document(doc_id).set, mood_data)
        logger.info(f"Mood log salvo para usuário '{user_id}'. Score: {mood_score}, Doc ID: {doc_id}")
    except Exception as e:
        logger.error(f"Erro ao salvar mood log para usuário '{user_id}': {e}", exc_info=True)

async def get_mood_logs(user_id: str, n: int = 7) -> list[dict]:
    """
    Recupera os últimos N registros de humor do usuário.
    
    Args:
        user_id: ID do usuário
        n: Número de registros a retornar
    
    Returns:
        Lista de dicionários com dados de mood logs
    """
    db = _initialize_firestore_client_instance()
    mood_logs = []

    try:
        mood_logs_ref = get_top_level_collection('mood_logs')
        query = mood_logs_ref.where("user_id", "==", user_id) \
            .order_by("timestamp", direction=firestore.Query.DESCENDING) \
            .limit(n)

        docs = await asyncio.to_thread(lambda: list(query.stream()))

        for doc in docs:
            mood_log = doc.to_dict()
            mood_log['id'] = doc.id
            mood_logs.append(mood_log)

        logger.info(f"Recuperados {len(mood_logs)} mood logs para usuário '{user_id}'.")
        return mood_logs
    except Exception as e:
        logger.error(f"Erro ao recuperar mood logs para usuário '{user_id}': {e}", exc_info=True)
        return []
# --- END OF FILE memory_utils.py ---