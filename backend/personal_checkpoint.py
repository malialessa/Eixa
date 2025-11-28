import datetime
import logging
import asyncio
from typing import Dict, Any

from collections_manager import get_top_level_collection
from firestore_utils import get_firestore_document_data, set_firestore_document
from weekly_summary import generate_weekly_summary # Deve ser async agora

logger = logging.getLogger(__name__)

async def run_weekly_checkpoint(user_id: str):
    logger.info(f"Running weekly personal checkpoint for user '{user_id}'.")

    try:
        summary_from_interactions = await generate_weekly_summary(user_id, 'interactions')
    except Exception as e:
        logger.error(f"Failed to generate weekly summary for user '{user_id}': {e}", exc_info=True)
        summary_from_interactions = "Não foi possível gerar um resumo detalhado das interações esta semana devido a um erro."

    achievements = []
    negative_patterns = []
    alerts = []

    summary_lower = summary_from_interactions.lower()
    if "tarefa adicionada" in summary_lower or "tarefas criadas" in summary_lower:
        achievements.append("Rastreamento ativo de novas tarefas e compromissos.")
    if "adiado" in summary_lower or "procrastinação" in summary_lower:
        alerts.append("Possível padrão de procrastinação detectado. Considere verificar as causas ou quebrar tarefas maiores.")
    if "cansaço" in summary_lower or "dispersão" in summary_lower or "esgotamento" in summary_lower:
        negative_patterns.append("Sinais de cansaço ou dispersão recorrentes observados. Priorize o descanso e o realinhamento do foco.")
    if "finalizou" in summary_lower or "concluiu" in summary_lower or "sucesso" in summary_lower:
        achievements.append("Progresso e conclusão de itens importantes detectados.")

    self_eval_doc_data = await get_firestore_document_data('self_eval', user_id)

    if not self_eval_doc_data:
        logger.info(f"Self-evaluation document not found for user '{user_id}'. Initializing default structure.")
        self_eval_doc_data = {
            "user_id": user_id,
            "eixa_performance_meta": {
                "interaction_frequency": "desconhecida",
                "perceived_utility": "desconhecida",
                "interaction_tone": "desconhecido",
                "detected_gaps": ["Nenhum gap de desempenho da EIXA detectado ainda."],
                "user_feedback": ["Nenhum feedback direto do usuário recebido ainda."],
                "last_review_date": None
            },
            "checkpoints": []
        }

    new_checkpoint = {
        "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
        "summary_text": summary_from_interactions,
        "achievements": achievements if achievements else ["Nenhum êxito específico detectado esta semana."],
        "negative_patterns": negative_patterns if negative_patterns else ["Nenhum padrão negativo detectado esta semana."],
        "alerts": alerts if alerts else ["Nenhum alerta específico detectado esta semana."],
        "llm_analysis_needed": True
    }

    self_eval_doc_data.setdefault("checkpoints", []).append(new_checkpoint)
    if len(self_eval_doc_data["checkpoints"]) > 10:
        self_eval_doc_data["checkpoints"].pop(0)

    await set_firestore_document('self_eval', user_id, self_eval_doc_data)
    logger.info(f"Weekly personal checkpoint saved for user '{user_id}'.")


async def get_latest_self_eval(user_id: str) -> Dict[str, Any]:
    self_eval_doc_data = await get_firestore_document_data('self_eval', user_id)

    if not self_eval_doc_data:
        logger.info(f"No self-evaluation document found for user '{user_id}'. Returning default empty structure.")
        return {
            "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
            "summary_text": "Nenhum resumo de diagnóstico disponível ainda. Interaja mais para que a EIXA possa gerar um.",
            "achievements": [],
            "negative_patterns": [],
            "alerts": []
        }

    checkpoints = self_eval_doc_data.get("checkpoints", [])

    if checkpoints:
        logger.info(f"Retrieved latest self-evaluation for user '{user_id}'.")
        return checkpoints[-1]

    logger.info(f"Self-evaluation document found for user '{user_id}', but no checkpoints recorded yet. Returning default empty structure.")
    return {
        "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
        "summary_text": "Nenhum resumo de diagnóstico disponível ainda (sem checkpoints registrados).",
        "achievements": [],
        "negative_patterns": [],
        "alerts": []
    }