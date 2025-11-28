import logging
import asyncio
from typing import List, Dict

import numpy as np
from google.cloud import firestore
# CORREÇÃO AQUI: Importe 'TextEmbeddingModel' do local correto
# Mudado de vertexai.preview.language_models para vertexai.language_models
from vertexai.language_models import TextEmbeddingModel # Importação corrigida para a versão estável!

# Importa o gerenciador de coleções e os utilitários do Firestore
from collections_manager import get_top_level_collection
from firestore_utils import set_firestore_document
from config import EMBEDDING_MODEL_NAME # Importe EMBEDDING_MODEL_NAME para o default

logger = logging.getLogger(__name__)

# --- Função para gerar embedding ---
# Adiciona model_name como parâmetro opcional, com default do config
async def get_embedding(text: str, project_id: str, location: str, model_name: str = EMBEDDING_MODEL_NAME) -> list[float] | None:
    try:
        # Acessa o modelo de embedding usando a classe importada corretamente
        model = TextEmbeddingModel.from_pretrained(model_name) # Usa o model_name passado ou default

        # CORREÇÃO: Mude 'predict' para 'get_embeddings'
        # model.get_embeddings retorna uma lista de objetos Embedding (no caso, um único objeto para um único texto).
        # Cada objeto Embedding tem um atributo 'values'.
        embeddings_response = await asyncio.to_thread(model.get_embeddings, [text])

        # Verifica se a resposta contém os valores do embedding corretamente
        # A resposta de get_embeddings é uma lista de objetos Embedding, então embeddings_response[0] é o primeiro Embedding object.
        if embeddings_response and embeddings_response[0] and hasattr(embeddings_response[0], 'values') and embeddings_response[0].values:
            return list(embeddings_response[0].values)
        return None
    except Exception as e:
        logger.error(f"Error generating embedding for text: '{text[:50]}...': {e}", exc_info=True)
        return None

# --- Funções de Armazenamento e Busca Vetorial no Firestore ---

async def add_memory_to_vectorstore(
    user_id: str,
    input_text: str,
    output_text: str,
    language: str,
    timestamp_for_doc_id: str,
    embedding: list[float]
):
    """
    Adiciona uma nova interação (memória) e seu embedding ao Firestore na coleção de embeddings.
    """
    embeddings_col_ref = get_top_level_collection('embeddings')

    doc_id = f"{user_id}_{timestamp_for_doc_id}"

    try:
        content = f"User: {input_text}\nAI: {output_text}"

        memory_data = {
            "user_id": user_id,
            "input": input_text,
            "output": output_text,
            "content": content,
            "language": language,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "embedding": embedding
        }
        await set_firestore_document('embeddings', doc_id, memory_data)
        logger.info(f"Interaction (vector memory) added to Firestore for user '{user_id}'. Doc ID: {doc_id}")

    except Exception as e:
        logger.error(f"Error adding vector memory to Firestore for user '{user_id}': {e}", exc_info=True)

async def get_relevant_memories(user_id: str, query_embedding: list[float], n_results: int = 3) -> List[Dict]:
    """
    Busca os N trechos de memória mais semanticamente similares no Firestore para um usuário específico.
    Calcula a similaridade de cosseno em memória para encontrar os chunks relevantes.
    """
    if not query_embedding:
        logger.debug(f"Query embedding is empty for user '{user_id}'. Returning empty list.")
        return []

    embeddings_col_ref = get_top_level_collection('embeddings')

    try:
        query = embeddings_col_ref.where('user_id', '==', user_id)
        docs = await asyncio.to_thread(lambda: list(query.stream()))

        if not docs:
            logger.debug(f"No vector memories found for user '{user_id}'. Returning empty list.")
            return []

        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            logger.warning(f"Query embedding norm is zero for user '{user_id}'. Cannot calculate similarity. Returning empty list.")
            return []

        similarities = []
        for doc in docs:
            memory = doc.to_dict()
            if 'embedding' in memory and memory['embedding'] is not None:
                try:
                    memory_vec = np.array(memory['embedding'], dtype=np.float32)
                    memory_norm = np.linalg.norm(memory_vec)

                    if memory_norm > 0:
                        similarity = np.dot(query_vec, memory_vec) / (query_norm * memory_norm)
                        similarities.append((similarity, memory))
                    else:
                        logger.warning(f"Memory embedding norm is zero for doc '{doc.id}' of user '{user_id}'. Skipping this memory.")
                except Exception as e:
                    logger.error(f"Error processing embedding for doc '{doc.id}' of user '{user_id}': {e}. Skipping this memory.", exc_info=True)
            else:
                logger.warning(f"Document '{doc.id}' of user '{user_id}' is missing or has a null 'embedding' field. Skipping this memory.")

        similarities.sort(key=lambda x: x[0], reverse=True)

        relevant_chunks = []
        for sim, memory in similarities[:n_results]:
            relevant_chunks.append({
                "content": memory.get('content', 'Conteúdo da memória não disponível'),
                "metadata": {
                    "user_id": memory.get('user_id', 'N/A'),
                    "input": memory.get('input', 'N/A'),
                    "output": memory.get('output', 'N/A'),
                    "language": memory.get('language', 'N/A'),
                    "timestamp": memory.get('timestamp', 'N/A')
                },
                "distance": 1 - sim
            })

        logger.debug(f"Retrieved {len(relevant_chunks)} similar chunks from Firestore for user '{user_id}'.")
        return relevant_chunks

    except Exception as e:
        logger.error(f"Error querying vector memories from Firestore for user '{user_id}': {e}", exc_info=True)
        return []