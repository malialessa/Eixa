import logging
import asyncio
from typing import List, Dict, Optional

import numpy as np
from metrics_utils import measure_async, record_latency
from google.cloud import firestore
# CORREÇÃO AQUI: Importe 'TextEmbeddingModel' do local correto
# Mudado de vertexai.preview.language_models para vertexai.language_models
from vertexai.language_models import TextEmbeddingModel # Importação corrigida para a versão estável!

# Importa o gerenciador de coleções e os utilitários do Firestore
from collections_manager import get_top_level_collection
from firestore_utils import set_firestore_document
from config import EMBEDDING_MODEL_NAME # Importe EMBEDDING_MODEL_NAME para o default
from bigquery_utils import bq_manager  # Usar BigQuery se disponível

logger = logging.getLogger(__name__)

# =====================
# LRU Cache simples para embeddings (evita recomputar textos repetidos de curta duração)
# =====================
class _LRUEmbeddingCache:
    def __init__(self, max_size: int = 128):
        self.max_size = max_size
        self._store: Dict[str, list[float]] = {}
        self._order: List[str] = []

    def get(self, key: str) -> Optional[list[float]]:
        if key in self._store:
            # Move para o fim (mais recente)
            try:
                self._order.remove(key)
            except ValueError:
                pass
            self._order.append(key)
            return self._store[key]
        return None

    def put(self, key: str, value: list[float]):
        if key in self._store:
            try:
                self._order.remove(key)
            except ValueError:
                pass
        self._store[key] = value
        self._order.append(key)
        if len(self._order) > self.max_size:
            oldest = self._order.pop(0)
            self._store.pop(oldest, None)

_embedding_cache = _LRUEmbeddingCache(max_size=128)

# --- Função para gerar embedding ---
# Adiciona model_name como parâmetro opcional, com default do config
@measure_async("vector.get_embedding")
async def get_embedding(text: str, project_id: str, location: str, model_name: str = EMBEDDING_MODEL_NAME) -> list[float] | None:
    try:
        cached = _embedding_cache.get(text)
        if cached is not None:
            return cached
        model = TextEmbeddingModel.from_pretrained(model_name)

        # CORREÇÃO: Mude 'predict' para 'get_embeddings'
        # model.get_embeddings retorna uma lista de objetos Embedding (no caso, um único objeto para um único texto).
        # Cada objeto Embedding tem um atributo 'values'.
        embeddings_response = await asyncio.to_thread(model.get_embeddings, [text])

        # Verifica se a resposta contém os valores do embedding corretamente
        # A resposta de get_embeddings é uma lista de objetos Embedding, então embeddings_response[0] é o primeiro Embedding object.
        if embeddings_response and embeddings_response[0] and hasattr(embeddings_response[0], 'values') and embeddings_response[0].values:
            emb = list(embeddings_response[0].values)
            # Normalização L2 + quantização leve para consistência
            import math
            norm = math.sqrt(sum(e*e for e in emb)) or 1.0
            emb = [round(e / norm, 4) for e in emb]
            _embedding_cache.put(text, emb)
            return emb
        return None
    except Exception as e:
        logger.error(f"Error generating embedding for text: '{text[:50]}...': {e}", exc_info=True)
        return None

# --- Funções de Armazenamento e Busca Vetorial no Firestore ---

@measure_async("vector.add_memory")
async def add_memory_to_vectorstore(
    user_id: str,
    input_text: str,
    output_text: str,
    language: str,
    timestamp_for_doc_id: str,
    embedding: list[float]
):
    """Adiciona memória vetorial.
    Fluxo:
      1. Se BigQuery disponível (bq_manager), insere em memory_embeddings.
      2. Também persiste no Firestore como fallback / auditoria.
    """
    content = f"User: {input_text}\nAI: {output_text}"
    memory_id = f"{user_id}_{timestamp_for_doc_id}"

    # BigQuery logging
    if bq_manager and embedding:
        try:
            await bq_manager.log_memory_embedding(
                user_id=user_id,
                memory_id=memory_id,
                content=content,
                input_text=input_text,
                output_text=output_text,
                language=language,
                embedding=embedding
            )
        except Exception as e:
            logger.error(f"Failed to log memory embedding in BigQuery for user '{user_id}': {e}", exc_info=True)

    # Firestore fallback
    embeddings_col_ref = get_top_level_collection('embeddings')
    try:
        memory_data = {
            "user_id": user_id,
            "input": input_text,
            "output": output_text,
            "content": content,
            "language": language,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "embedding": embedding
        }
        await set_firestore_document('embeddings', memory_id, memory_data)
        logger.info(f"Vector memory stored (Firestore) user='{user_id}' id='{memory_id}'")
    except Exception as e:
        logger.error(f"Error adding vector memory to Firestore for user '{user_id}': {e}", exc_info=True)

@measure_async("vector.get_relevant_memories")
async def get_relevant_memories(user_id: str, query_embedding: list[float], n_results: int = 3) -> List[Dict]:
    """Busca memórias relevantes.
    Prioridade: BigQuery (se bq_manager e embedding). Fallback: Firestore busca linear.
    """
    if not query_embedding:
        logger.debug(f"Query embedding is empty for user '{user_id}'. Returning empty list.")
        return []

    # Tenta BigQuery primeiro
    if bq_manager:
        try:
            bq_results = await bq_manager.search_memory_embeddings(user_id=user_id, query_embedding=query_embedding, top_k=n_results)
            if bq_results:
                formatted = []
                for r in bq_results:
                    formatted.append({
                        "content": r.get("content", "Conteúdo não disponível"),
                        "metadata": {
                            "user_id": user_id,
                            "input": r.get("input"),
                            "output": r.get("output"),
                            "language": r.get("language"),
                            "created_at": r.get("created_at"),
                            "memory_id": r.get("memory_id")
                        },
                        "distance": r.get("distance")
                    })
                logger.debug(f"Retrieved {len(formatted)} similar chunks from BigQuery for user '{user_id}'.")
                record_latency("vector.retrieval.bq", 0.0, True, {"count": len(formatted)})
                # Log hit ratio inicial (all retrieved; usage será definido externamente caso use subset)
                try:
                    await bq_manager.log_memory_hits(user_id, [m['metadata']['memory_id'] for m in formatted if 'metadata' in m and 'memory_id' in m['metadata']], [])
                except Exception:
                    pass
                return formatted
        except Exception as e:
            logger.error(f"BigQuery similarity search failed for user '{user_id}': {e}. Falling back to Firestore.", exc_info=True)

    # Fallback Firestore
    embeddings_col_ref = get_top_level_collection('embeddings')
    try:
        query = embeddings_col_ref.where('user_id', '==', user_id)
        docs = await asyncio.to_thread(lambda: list(query.stream()))
        if not docs:
            return []

        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        scored = []
        for doc in docs:
            memory = doc.to_dict()
            emb = memory.get('embedding')
            if not emb:
                continue
            try:
                mem_vec = np.array(emb, dtype=np.float32)
                mem_norm = np.linalg.norm(mem_vec)
                if mem_norm == 0:
                    continue
                sim = float(np.dot(query_vec, mem_vec) / (query_norm * mem_norm))
                scored.append((sim, memory))
            except Exception as e:
                logger.error(f"Error processing doc '{doc.id}' embedding for user '{user_id}': {e}", exc_info=True)

        scored.sort(key=lambda x: x[0], reverse=True)
        result = []
        for sim, memory in scored[:n_results]:
            result.append({
                "content": memory.get('content', 'Conteúdo da memória não disponível'),
                "metadata": {
                    "user_id": memory.get('user_id'),
                    "input": memory.get('input'),
                    "output": memory.get('output'),
                    "language": memory.get('language'),
                    "timestamp": memory.get('timestamp')
                },
                "distance": 1 - sim
            })
        logger.debug(f"Retrieved {len(result)} similar chunks from Firestore (fallback) for user '{user_id}'.")
        record_latency("vector.retrieval.firestore", 0.0, True, {"count": len(result)})
        return result
    except Exception as e:
        logger.error(f"Error querying vector memories from Firestore for user '{user_id}': {e}", exc_info=True)
        return []