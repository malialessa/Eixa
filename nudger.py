import datetime
import logging
from typing import Dict, Any, List
import asyncio
# import numpy as np # Não utilizado diretamente, pode ser removido se não houver lógica de embedding aqui

from collections_manager import get_top_level_collection
from firestore_utils import get_firestore_document_data, set_firestore_document

logger = logging.getLogger(__name__)

async def get_nudger_state(user_id: str) -> Dict[str, Any]:
    """
    Recupera o estado do nudger para um usuário do Firestore de forma assíncrona.
    """
    state_doc_data = await get_firestore_document_data('nudger', user_id)

    if not state_doc_data:
        logger.info(f"Nudger state not found for user '{user_id}'. Initializing default state.")
        state_doc_data = {
            "last_nudge_timestamp": None,
            "last_interaction_timestamp": None,
            "repeated_topics_last_hour": {}, # Ex: {topic: count}
            "procrastination_flags": {},     # Ex: {task_id: last_procrastination_date}
            "pattern_consent": {},           # Ex: {pattern_name: True/False} para nudges específicos
            "last_strategic_nudge_timestamp": None # Para nudges mais complexos e menos frequentes
        }
    return state_doc_data

async def save_nudger_state(user_id: str, state: Dict[str, Any]):
    """
    Salva o estado atual do nudger para um usuário no Firestore de forma assíncrona.
    """
    state["last_update_timestamp"] = datetime.datetime.now(datetime.timezone.utc)
    await set_firestore_document('nudger', user_id, state)


async def check_for_inactivity_nudge(nudger_state: Dict[str, Any]) -> str:
    """
    Verifica se há inatividade que justifique um nudge de retomada (ex: 30 minutos).
    """
    last_interaction_ts = nudger_state.get("last_interaction_timestamp")
    if not last_interaction_ts:
        logger.debug(f"No last_interaction_timestamp in nudger state. Skipping inactivity check.")
        return ""

    now = datetime.datetime.now(datetime.timezone.utc)

    # Garante que o timestamp do Firestore seja timezone-aware para comparação correta.
    if isinstance(last_interaction_ts, datetime.datetime) and last_interaction_ts.tzinfo is None:
        last_interaction_ts = last_interaction_ts.replace(tzinfo=datetime.timezone.utc)

    if (now - last_interaction_ts).total_seconds() > 1800:
        logger.info(f"Inactivity detected for user. Last interaction: {last_interaction_ts.isoformat()}. Sending inactivity nudge.")
        return "Parece que você está um pouco afastado(a). Precisa de ajuda para retomar o fluxo ou organizar algo?"

    return ""

async def analyze_for_nudges(
    user_id: str,
    user_message: str,
    history: List[Dict[str, Any]], # Espera histórico já ordenado do mais recente para o mais antigo
    user_flags: Dict[str, Any],
    user_profile: Dict[str, Any] = None # Perfil completo do usuário para nudges personalizados
) -> str:
    """
    Analisa interações e perfil do usuário para gerar "nudges" inteligentes.
    Retorna uma mensagem de nudge se aplicável, ou uma string vazia.
    """
    if user_flags.get("silent_mode", False):
        logger.info(f"Nudging is in silent_mode for user '{user_id}'. No nudges will be generated.")
        return ""

    nudges = []
    nudger_state = await get_nudger_state(user_id)

    # --- 1. Lógica de Nudging Base (Independente do Perfil Detalhado) ---
    inactivity_nudge = await check_for_inactivity_nudge(nudger_state)
    if inactivity_nudge:
        nudges.append(inactivity_nudge)

    if len(history) >= 2 and history[0].get('input', '').strip().lower() == history[1].get('input', '').strip().lower():
        nudges.append("Notei que você repetiu sua última mensagem. Está tudo bem ou posso ajudar de outra forma?")

    # --- 2. Lógica de Nudging Aprimorada com Perfil do Usuário ---
    if user_profile:
        user_message_lower = user_message.lower()
        psych_profile = user_profile.get('psychological_profile', {})
        daily_routine = user_profile.get('daily_routine_elements', {})
        comm_prefs = user_profile.get('communication_preferences', {})
        eixa_prefs = user_profile.get('eixa_interaction_preferences', {})

        # Nudges baseados em rotina e alertas de bem-estar
        alerts_and_reminders = daily_routine.get('alerts_and_reminders', {})
        if alerts_and_reminders.get('hydration') and "sede" in user_message_lower:
            nudges.append(f"Você mencionou sede. Lembre-se da sua meta de hidratação: {alerts_and_reminders['hydration']}!")
        if alerts_and_reminders.get('eye_strain') and ("cansaço visual" in user_message_lower or "tela demais" in user_message_lower):
            nudges.append(f"Seus olhos estão cansados? Lembre-se da sua orientação de {alerts_and_reminders['eye_strain']} para a saúde ocular.")

        # Nudges baseados em condições/histórico de padrões comportamentais
        diagnoses_and_conditions = psych_profile.get('diagnoses_and_conditions', [])
        historical_behavioral_patterns = psych_profile.get('historical_behavioral_patterns', [])

        if "Transtorno_de_Humor_Bipolar" in diagnoses_and_conditions:
            if any(term in user_message_lower for term in ["muita energia", "não consigo parar", "meio acelerado"]):
                nudges.append("Percebo que você está com muita energia. Podemos canalizar isso para as suas prioridades, mas lembre-se também da importância do equilíbrio e da calma.")
            elif any(term in user_message_lower for term in ["sem energia", "desanimado", "só quero ficar na cama"]):
                 nudges.append("Sinto que você está sem energia hoje. Lembre-se que é ok ter dias assim. Vamos focar em um passo pequeno para reacender seu fluxo, ou prefere apenas conversar?")

        if "ciclos_de_hiperfoco_seguidos_de_esgotamento" in historical_behavioral_patterns:
            if len(history) > 3 and all(len(h.get('input', '').split()) > 10 for h in history[:3]) and len(user_message.split()) > 20:
                 nudges.append("Percebo sua concentração intensa e o quanto você está focada(o) neste tópico. Lembre-se do seu padrão de hiperfoco seguido de esgotamento. Que tal uma breve pausa, ou quebrar a tarefa em partes menores?")

        if "abandono_de_projetos_longos" in historical_behavioral_patterns:
            current_projects = user_profile.get('current_projects', [])
            if current_projects and any(p.get('name', '').lower() in user_message_lower for p in current_projects[:1]):
                nudges.append(f"Falando em '{current_projects[0].get('name', 'seus projetos')}', como ele está? Lembre-se do seu padrão de 'abandono de projetos', podemos pensar em como mantê-lo em movimento, mesmo com um pequeno passo.")

        if "overcommitment_e_dificuldade_em_dizer_não" in historical_behavioral_patterns:
            if ("adicionar tarefa" in user_message_lower or "crie projeto" in user_message_lower) and len(history) < 5:
                nudges.append("Notei que você está adicionando bastante coisa. Lembre-se do seu padrão de overcommitment. Podemos revisar suas prioridades para ter certeza de que tudo se encaixa na sua capacidade atual?")

        alerts_and_reminders = daily_routine.get('alerts_and_reminders', {}) # Recarrega para usar os triggers
        overwhelm_triggers = alerts_and_reminders.get('overwhelm_triggers', [])
        if any(trigger.lower().replace("_", " ") in user_message_lower for trigger in overwhelm_triggers):
            nudges.append("Sinto que você pode estar se sentindo sobrecarregado(a). Seus gatilhos de sobrecarga (como 'muitas notificações') foram mencionados. Que tal uma pausa ou focar em uma coisa por vez?")

        burnout_indicators = alerts_and_reminders.get('burnout_indicators', [])
        if any(indicator.lower().replace("_", " ") in user_message_lower for indicator in burnout_indicators):
            nudges.append(f"Seus indicadores de burnout (como '{burnout_indicators[0].replace('_', ' ')}') foram detectados. É crucial cuidar do seu bem-estar. Que tal reduzir a carga ou focar em algo relaxante?")

        expected_eixa_actions = eixa_prefs.get('expected_eixa_actions', [])
        if "propor_divisao_de_tarefas_grandes_em_passos_menores" in expected_eixa_actions and ("tarefa grande" in user_message_lower or "complexo" in user_message_lower):
            nudges.append("Essa tarefa parece grande. Você gostaria que a dividíssemos em passos menores para facilitar o início?")

    nudger_state["last_interaction_timestamp"] = datetime.datetime.now(datetime.timezone.utc)
    await save_nudger_state(user_id, nudger_state)

    return " ".join([n for n in nudges if n]).strip()