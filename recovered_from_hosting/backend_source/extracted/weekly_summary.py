import datetime
from google.cloud import firestore 
import logging
import asyncio

from collections_manager import get_top_level_collection

logger = logging.getLogger(__name__)

async def generate_weekly_summary(user_id: str, collection_logical_name: str) -> str:
    logger.info(f"Generating weekly summary for user '{user_id}' from collection '{collection_logical_name}'.")

    end_date = datetime.datetime.now(datetime.timezone.utc)
    start_date = end_date - datetime.timedelta(weeks=1)

    try:
        interactions_ref = get_top_level_collection(collection_logical_name)
    except KeyError as e:
        logger.error(f"Erro ao gerar resumo semanal: {e}. Nome lógico de coleção inválido '{collection_logical_name}'.", exc_info=True)
        return f"Não foi possível gerar o resumo semanal: nome de coleção '{collection_logical_name}' inválido."
    except Exception as e:
        logger.error(f"Erro inesperado ao obter referência da coleção '{collection_logical_name}': {e}", exc_info=True)
        return "Não foi possível gerar o resumo semanal devido a um erro interno na configuração da coleção."


    # Evita dependência de índices compostos ao buscar por intervalo de timestamp.
    # Primeiro filtra por período e, em seguida, aplica filtro por usuário em memória.
    query = interactions_ref\
        .where('timestamp', '>=', start_date)\
        .where('timestamp', '<=', end_date)

    recent_interactions = []
    try:
        docs = await asyncio.to_thread(lambda: list(query.stream()))
        for doc in docs:
            data = doc.to_dict()
            if data.get('user_id') != user_id:
                continue
            recent_interactions.append({
                "input": data.get('input', '[Sem input]'),
                "output": data.get('output', '[Sem output]'),
                "timestamp": data.get('timestamp')
            })
    except Exception as e:
        logger.error(f"Erro ao buscar interações semanais para o resumo do usuário '{user_id}': {e}", exc_info=True)
        return "Não foi possível gerar o resumo semanal devido a um erro na recuperação do histórico."

    recent_interactions.sort(key=lambda entry: entry.get('timestamp') or datetime.datetime.min)

    if not recent_interactions:
        return f"Não há interações registradas para o usuário '{user_id}' na última semana ({start_date.strftime('%d/%m')} - {end_date.strftime('%d/%m')})."

    summary_text = f"Resumo semanal de interações com o usuário '{user_id}' ({start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}):\n\n"
    summary_text += "Este é um resumo bruto das últimas interações:\n"
    for interaction in recent_interactions:
        summary_text += "- Usuário: {user}\n  EIXA: {assistant}\n\n".format(
            user=interaction.get('input', '[Sem input]'),
            assistant=interaction.get('output', '[Sem output]')
        )
    summary_text += "\nPara um resumo inteligente, esta informação precisaria ser processada por um LLM."

    logger.info(f"Weekly summary generated for user '{user_id}'.")
    return summary_text