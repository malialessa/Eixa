import datetime
import logging
from typing import List, Dict
import asyncio 

from collections_manager import get_top_level_collection
from firestore_utils import get_firestore_document_data, set_firestore_document

logger = logging.getLogger(__name__)

async def get_user_behavior_data(user_id: str) -> dict: # Função agora assíncrona
    data = await asyncio.to_thread(get_firestore_document_data, 'behavior', user_id) 
    
    if not data:
        data = {
            "last_active_timestamp": None,
            "repetition_count": 0,
            "procrastinated_tasks": {}, 
            "pattern_flags": {}      
        }
    return data

async def save_user_behavior_data(user_id: str, data: dict): # Função agora assíncrona
    await asyncio.to_thread(set_firestore_document, 'behavior', user_id, data)


def detect_inactivity(user_id: str, last_interaction_timestamp: datetime.datetime, threshold_minutes: int = 60) -> bool:
    if not last_interaction_timestamp:
        logger.debug(f"No last_interaction_timestamp for user '{user_id}'. Cannot detect inactivity.")
        return False
        
    if last_interaction_timestamp.tzinfo is None:
        last_interaction_timestamp = last_interaction_timestamp.replace(tzinfo=datetime.timezone.utc)

    now = datetime.datetime.now(datetime.timezone.utc)
    inactivity_duration = now - last_interaction_timestamp

    if inactivity_duration.total_seconds() > (threshold_minutes * 60):
        logger.info(f"Inatividade detectada para o usuário '{user_id}': {inactivity_duration.total_seconds() / 60:.1f} minutos.")
        return True
    return False

async def track_repetition(user_id: str, current_message: str, history: List[Dict]): # Função agora assíncrona
    if not current_message or not isinstance(history, list):
        logger.warning(f"Invalid input for track_repetition for user '{user_id}'. Skipping.")
        return
        
    user_behavior = await get_user_behavior_data(user_id) # Chama a versão assíncrona

    if history and current_message.strip().lower() == history[0].get('input', '').strip().lower(): 
        user_behavior["repetition_count"] = user_behavior.get("repetition_count", 0) + 1
        logger.warning(f"Repetição detectada para o usuário '{user_id}'. Contagem: {user_behavior['repetition_count']}")
    else:
        user_behavior["repetition_count"] = 0
        logger.debug(f"Repetition count reset for user '{user_id}'.")

    await save_user_behavior_data(user_id, user_behavior) # Chama a versão assíncrona

def schedule_silent_checkpoints():
    logger.info("Funcionalidade para agendamento de checkpoints silenciosos residiria aqui (acionada por Cloud Scheduler ou similar).")