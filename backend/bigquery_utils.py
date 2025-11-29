"""
BigQuery Utilities for EIXA
Provides data warehouse capabilities for analytics and historical data
"""
import logging
import asyncio
import time
from datetime import datetime, timezone
from typing import List, Dict, Any
from google.cloud import bigquery
from google.api_core import retry
from metrics_utils import measure_async, set_bq_manager, record_latency

logger = logging.getLogger(__name__)

class BigQueryManagerBase:
    def __init__(self, project_id: str, dataset_id: str = "eixa"):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.client = bigquery.Client(project=project_id)
        self.dataset_ref = f"{project_id}.{dataset_id}"

    async def ensure_dataset_exists(self):
        try:
            await asyncio.to_thread(self.client.get_dataset, self.dataset_ref)
            return
        except Exception:
            ds = bigquery.Dataset(self.dataset_ref)
            ds.location = "us-east1"
            await asyncio.to_thread(self.client.create_dataset, ds, exists_ok=True)
            logger.info(f"Dataset '{self.dataset_ref}' ensured")

    async def create_tables_if_not_exist(self):
        tables = {
            "user_interactions": [
                bigquery.SchemaField("user_id", "STRING"),
                bigquery.SchemaField("interaction_id", "STRING"),
                bigquery.SchemaField("timestamp", "TIMESTAMP"),
                bigquery.SchemaField("message_in", "STRING"),
                bigquery.SchemaField("message_out", "STRING"),
                bigquery.SchemaField("intent", "STRING"),
                bigquery.SchemaField("language", "STRING"),
                bigquery.SchemaField("duration_ms", "INT64"),
                bigquery.SchemaField("model_used", "STRING"),
                bigquery.SchemaField("tokens_in", "INT64"),
                bigquery.SchemaField("tokens_out", "INT64"),
                bigquery.SchemaField("error_code", "STRING"),
            ],
            "tasks": [
                bigquery.SchemaField("user_id", "STRING"),
                bigquery.SchemaField("task_id", "STRING"),
                bigquery.SchemaField("description", "STRING"),
                bigquery.SchemaField("date", "DATE"),
                bigquery.SchemaField("time", "TIME"),
                bigquery.SchemaField("duration_minutes", "INT64"),
                bigquery.SchemaField("completed", "BOOL"),
                bigquery.SchemaField("origin", "STRING"),
                bigquery.SchemaField("routine_item_id", "STRING"),
                bigquery.SchemaField("google_calendar_event_id", "STRING"),
                bigquery.SchemaField("created_at", "TIMESTAMP"),
                bigquery.SchemaField("updated_at", "TIMESTAMP"),
            ],
            "emotional_memories": [
                bigquery.SchemaField("user_id", "STRING"),
                bigquery.SchemaField("memory_id", "STRING"),
                bigquery.SchemaField("description", "STRING"),
                bigquery.SchemaField("emotional_valence", "FLOAT64"),
                bigquery.SchemaField("trigger", "STRING"),
                bigquery.SchemaField("context", "STRING"),
                bigquery.SchemaField("timestamp", "TIMESTAMP"),
            ],
        }
        for name, schema in tables.items():
            table_id = f"{self.dataset_ref}.{name}"
            try:
                await asyncio.to_thread(self.client.get_table, table_id)
            except Exception:
                tbl = bigquery.Table(table_id, schema=schema)
                tbl.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field="timestamp" if name=="user_interactions" else None)
                await asyncio.to_thread(self.client.create_table, tbl, exists_ok=True)
                logger.info(f"Created table '{table_id}'")

class BigQueryManager(BigQueryManagerBase):  # redefine incorporando métodos avançados
    """Extensão da BigQueryManager com recursos de embeddings, batch e métricas.
    Esta redefinição mantém compatibilidade com instâncias existentes mas adiciona:
      - Criação de tabela memory_embeddings (VECTOR ou ARRAY)
      - Inserção com batch buffer
      - Busca vetorial (VECTOR_DISTANCE ou cosseno manual)
      - Registro de hits (memórias retornadas/usadas)
      - Registro de métricas de operação
    """

    def __init__(self, project_id: str, dataset_id: str = "eixa"):
        super().__init__(project_id, dataset_id)
        # Buffer para batch de embeddings
        self._embedding_buffer: List[Dict[str, Any]] = []
        self._embedding_buffer_max = 50  # Tamanho máximo antes de flush automático
        self._embedding_buffer_interval_sec = 15  # Intervalo para flush temporal
        self._last_flush_ts = datetime.now(timezone.utc)

    @measure_async("bq.ensure_memory_embeddings_table")
    async def ensure_memory_embeddings_table(self):
        """Cria tabela de embeddings de memória se não existir. Tenta VECTOR e fallback ARRAY.
        Adiciona coluna memory_type se ausente.
        """
        full_table_id = _memory_embeddings_table_ref(self.project_id, self.dataset_id)
        if not await _table_exists(self.client, full_table_id):
            try:
                table_vector = _build_memory_embeddings_table('vector', full_table_id)
                await asyncio.to_thread(self.client.create_table, table_vector)
                logger.info("memory_embeddings table created with VECTOR type")
            except Exception as e:
                logger.warning(f"VECTOR creation failed ({e}). Using ARRAY<FLOAT64>.")
                table_array = _build_memory_embeddings_table('array', full_table_id)
                await asyncio.to_thread(self.client.create_table, table_array, exists_ok=True)
                logger.info("memory_embeddings table created with ARRAY<FLOAT64> type")
        # Garantir coluna extra memory_type
        try:
            table = await asyncio.to_thread(self.client.get_table, full_table_id)
            if not any(f.name == 'memory_type' for f in table.schema):
                await asyncio.to_thread(self.client.query, f"ALTER TABLE `{full_table_id}` ADD COLUMN memory_type STRING")
                logger.info("Added column memory_type to memory_embeddings")
        except Exception as e:
            logger.warning(f"Could not add memory_type column: {e}")

    @measure_async("bq.log_memory_embedding")
    async def log_memory_embedding(self, user_id: str, memory_id: str, content: str, input_text: str,
                                   output_text: str, language: str, embedding: List[float],
                                   memory_type: str = None, use_batch: bool = True,
                                   normalize: bool = True, quantize: bool = True) -> None:
        """Registra uma memória vetorial.
        Args:
          use_batch: se True adiciona ao buffer antes de flush.
          normalize: normaliza vetor L2.
          quantize: aplica quantização simples (arredonda 4 casas) para reduzir tamanho.
        """
        if normalize and embedding:
            import math
            norm = math.sqrt(sum(e*e for e in embedding)) or 1.0
            embedding = [e / norm for e in embedding]
        if quantize and embedding:
            embedding = [round(e, 4) for e in embedding]
        row = {
            "user_id": user_id,
            "memory_id": memory_id,
            "content": content,
            "input": input_text,
            "output": output_text,
            "language": language,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "embedding": embedding,
            "memory_type": memory_type or "generic",
        }
        if use_batch:
            self._embedding_buffer.append(row)
            now = datetime.now(timezone.utc)
            if len(self._embedding_buffer) >= self._embedding_buffer_max or (now - self._last_flush_ts).total_seconds() >= self._embedding_buffer_interval_sec:
                await self.flush_embedding_buffer()
        else:
            await self._insert_rows(_memory_embeddings_table_ref(self.project_id, self.dataset_id), [row])

    @measure_async("bq.flush_embedding_buffer")
    async def flush_embedding_buffer(self):
        """Força flush do buffer de embeddings para BigQuery."""
        if not self._embedding_buffer:
            return
        table_id = _memory_embeddings_table_ref(self.project_id, self.dataset_id)
        batch = self._embedding_buffer[:]
        self._embedding_buffer.clear()
        await self._insert_rows(table_id, batch)
        self._last_flush_ts = datetime.now(timezone.utc)
        logger.debug(f"Flushed {len(batch)} embeddings to BigQuery")

    @measure_async("bq.insert_rows")
    async def _insert_rows(self, table_id: str, rows: List[Dict[str, Any]]):
        try:
            errors = await asyncio.to_thread(self.client.insert_rows_json, table_id, rows, retry=retry.Retry(deadline=30))
            if errors:
                logger.error(f"BigQuery insert errors ({table_id}): {errors}")
        except Exception as e:
            logger.error(f"Error inserting rows into {table_id}: {e}", exc_info=True)

    @measure_async("bq.search_memory_embeddings")
    async def search_memory_embeddings(self, user_id: str, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """Busca memórias similares; normaliza query para cosseno se ARRAY."""
        table_id = _memory_embeddings_table_ref(self.project_id, self.dataset_id)
        try:
            table = await asyncio.to_thread(self.client.get_table, table_id)
        except Exception as e:
            logger.error(f"Cannot get memory_embeddings table: {e}", exc_info=True)
            return []
        embedding_field = next((f for f in table.schema if f.name == 'embedding'), None)
        if not embedding_field:
            return []
        is_vector = embedding_field.field_type.upper() == 'VECTOR'
        if not query_embedding:
            return []
        if not is_vector:
            import math
            norm = math.sqrt(sum(e*e for e in query_embedding)) or 1.0
            query_embedding = [e / norm for e in query_embedding]
        if is_vector:
            query = f"""
            SELECT memory_id, content, input, output, language, created_at, memory_type,
                   VECTOR_DISTANCE(embedding, @query_vec) AS distance
            FROM `{table_id}`
            WHERE user_id = @user_id
            ORDER BY distance ASC
            LIMIT @top_k
            """
            params = [
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ArrayQueryParameter("query_vec", "FLOAT64", query_embedding),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k)
            ]
        else:
            query = f"""
            SELECT memory_id, content, input, output, language, created_at, memory_type,
                (
                  (SELECT SUM(a*b) FROM UNNEST(embedding) a WITH OFFSET i JOIN UNNEST(@query_embedding) b WITH OFFSET j ON i=j)
                ) AS dot,
                SQRT((SELECT SUM(a*a) FROM UNNEST(embedding) a)) AS mem_norm
            FROM `{table_id}`
            WHERE user_id = @user_id
            """
            params = [
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ArrayQueryParameter("query_embedding", "FLOAT64", query_embedding)
            ]
        try:
            job = await asyncio.to_thread(self.client.query, query, job_config=bigquery.QueryJobConfig(query_parameters=params))
            rows = await asyncio.to_thread(job.result)
            results = []
            for r in rows:
                if is_vector:
                    distance = r.distance
                    similarity = None
                else:
                    # mem_norm pode ser 0 (evitar divisão)
                    mem_norm = r.mem_norm or 1.0
                    dot = r.dot or 0.0
                    similarity = dot / mem_norm  # query já normalizada
                    distance = 1 - similarity
                results.append({
                    "memory_id": r.memory_id,
                    "content": r.content,
                    "input": r.input,
                    "output": r.output,
                    "language": r.language,
                    "created_at": r.created_at.isoformat() if hasattr(r.created_at, 'isoformat') else str(r.created_at),
                    "distance": distance,
                    "similarity": similarity,
                    "memory_type": r.memory_type
                })
            return sorted(results, key=lambda x: x['distance'])[:top_k]
        except Exception as e:
            logger.error(f"Error searching memory embeddings: {e}", exc_info=True)
            return []

    async def log_memory_hits(self, user_id: str, retrieved_ids: List[str], used_ids: List[str]):
        """Registra métricas de hit ratio em tabela dedicada."""
        table_id = f"{self.project_id}.{self.dataset_id}.memory_embedding_hits"
        # Criar tabela se não existir
        try:
            await asyncio.to_thread(self.client.get_table, table_id)
        except Exception:
            schema = [
                bigquery.SchemaField("user_id", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
                bigquery.SchemaField("retrieved_ids", "STRING", mode="REPEATED"),
                bigquery.SchemaField("used_ids", "STRING", mode="REPEATED"),
                bigquery.SchemaField("hit_ratio", "FLOAT64")
            ]
            tbl = bigquery.Table(table_id, schema=schema)
            tbl.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field="timestamp")
            await asyncio.to_thread(self.client.create_table, tbl, exists_ok=True)
        hit_ratio = (len(set(used_ids)) / len(retrieved_ids)) if retrieved_ids else 0.0
        row = {
            "user_id": user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "retrieved_ids": retrieved_ids,
            "used_ids": used_ids,
            "hit_ratio": round(hit_ratio, 4)
        }
        await self._insert_rows(table_id, [row])

    async def log_operation_metric(self, operation: str, duration_ms: float, success: bool, extra: Dict[str, Any] | None = None):
        """Registra métrica simples de operação (latência, sucesso)."""
        table_id = f"{self.project_id}.{self.dataset_id}.operation_metrics"
        try:
            await asyncio.to_thread(self.client.get_table, table_id)
        except Exception:
            schema = [
                bigquery.SchemaField("operation", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
                bigquery.SchemaField("duration_ms", "FLOAT64"),
                bigquery.SchemaField("success", "BOOL"),
                bigquery.SchemaField("extra_json", "STRING")
            ]
            tbl = bigquery.Table(table_id, schema=schema)
            tbl.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field="timestamp")
            await asyncio.to_thread(self.client.create_table, tbl, exists_ok=True)
        row = {
            "operation": operation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "success": success,
            "extra_json": (str(extra) if extra else None)
        }
        await self._insert_rows(table_id, [row])

    @measure_async("bq.log_interaction")
    async def log_interaction(
        self,
        user_id: str,
        interaction_id: str,
        message_in: str,
        message_out: str,
        intent: str = None,
        language: str = "pt",
        duration_ms: int = None,
        model_used: str = None,
        tokens_in: int = None,
        tokens_out: int = None,
        error_code: str = None
    ):
        """Loga interação do usuário para analytics/RAG."""
        table_id = f"{self.project_id}.{self.dataset_id}.user_interactions"
        row = {
            "user_id": user_id,
            "interaction_id": interaction_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message_in": message_in,
            "message_out": message_out,
            "intent": intent,
            "language": language,
            "duration_ms": duration_ms,
            "model_used": model_used,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "error_code": error_code,
        }
        try:
            errors = await asyncio.to_thread(
                self.client.insert_rows_json,
                table_id,
                [row],
                retry=retry.Retry(deadline=30)
            )
            if errors:
                logger.error(f"BigQuery insert errors (interactions): {errors}")
            else:
                logger.debug(f"Interaction logged to BigQuery: {interaction_id}")
        except Exception as e:
            logger.error(f"Error logging interaction to BigQuery: {e}", exc_info=True)
    
    async def log_task(self, user_id: str, task_data: Dict[str, Any]):
        """Log task to BigQuery"""
        table_id = f"{self.dataset_ref}.tasks"
        
        rows = [{
            "user_id": user_id,
            "task_id": task_data.get("id"),
            "description": task_data.get("description"),
            "date": task_data.get("date"),
            "time": task_data.get("time"),
            "duration_minutes": task_data.get("duration_minutes"),
            "completed": task_data.get("completed", False),
            "origin": task_data.get("origin", "user_added"),
            "routine_item_id": task_data.get("routine_item_id"),
            "google_calendar_event_id": task_data.get("google_calendar_event_id"),
            "created_at": task_data.get("created_at", datetime.now(timezone.utc).isoformat()),
            "updated_at": task_data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        }]
        
        try:
            errors = await asyncio.to_thread(
                self.client.insert_rows_json,
                table_id,
                rows,
                retry=retry.Retry(deadline=30)
            )
            if errors:
                logger.error(f"BigQuery insert errors (tasks): {errors}")
        except Exception as e:
            logger.error(f"Error logging task to BigQuery: {e}", exc_info=True)
    
    async def log_emotional_memory(self, user_id: str, memory_data: Dict[str, Any]):
        """Log emotional memory to BigQuery"""
        table_id = f"{self.dataset_ref}.emotional_memories"
        
        rows = [{
            "user_id": user_id,
            "memory_id": memory_data.get("id"),
            "description": memory_data.get("description"),
            "emotional_valence": memory_data.get("emotional_valence"),
            "trigger": memory_data.get("trigger"),
            "context": memory_data.get("context"),
            "timestamp": memory_data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        }]
        
        try:
            errors = await asyncio.to_thread(
                self.client.insert_rows_json,
                table_id,
                rows,
                retry=retry.Retry(deadline=30)
            )
            if errors:
                logger.error(f"BigQuery insert errors (memories): {errors}")
        except Exception as e:
            logger.error(f"Error logging memory to BigQuery: {e}", exc_info=True)
    
    async def query_user_analytics(self, user_id: str, days: int = 30) -> Dict[str, Any]:
        """Get user analytics for the last N days"""
        query = f"""
        SELECT
            COUNT(*) as total_interactions,
            COUNT(DISTINCT DATE(timestamp)) as active_days,
            AVG(duration_ms) as avg_duration_ms,
            SUM(tokens_in + tokens_out) as total_tokens,
            COUNTIF(error_code IS NOT NULL) as error_count,
            APPROX_TOP_COUNT(intent, 5) as top_intents
        FROM `{self.dataset_ref}.user_interactions`
        WHERE user_id = @user_id
            AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        """
        
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ScalarQueryParameter("days", "INT64", days),
            ]
        )
        
        try:
            query_job = await asyncio.to_thread(
                self.client.query,
                query,
                job_config=job_config
            )
            results = await asyncio.to_thread(query_job.result)
            
            for row in results:
                return {
                    "total_interactions": row.total_interactions,
                    "active_days": row.active_days,
                    "avg_duration_ms": row.avg_duration_ms,
                    "total_tokens": row.total_tokens,
                    "error_count": row.error_count,
                    "top_intents": [{"value": item.value, "count": item.count} for item in row.top_intents],
                }
            return {}
        except Exception as e:
            logger.error(f"Error querying user analytics: {e}", exc_info=True)
            return {}
    
    async def get_task_completion_rate(self, user_id: str, days: int = 7) -> float:
        """Get task completion rate for last N days"""
        query = f"""
        SELECT
            COUNTIF(completed) / COUNT(*) * 100 as completion_rate
        FROM `{self.dataset_ref}.tasks`
        WHERE user_id = @user_id
            AND created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        """
        
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ScalarQueryParameter("days", "INT64", days),
            ]
        )
        
        try:
            query_job = await asyncio.to_thread(
                self.client.query,
                query,
                job_config=job_config
            )
            results = await asyncio.to_thread(query_job.result)
            
            for row in results:
                return row.completion_rate or 0.0
            return 0.0
        except Exception as e:
            logger.error(f"Error querying completion rate: {e}", exc_info=True)
            return 0.0


# Global instance (initialize in main.py)
bq_manager: BigQueryManager | None = None


def initialize_bigquery(project_id: str):
    """Initialize global BigQuery manager (extended) e injeta em métricas."""
    global bq_manager
    start = time.perf_counter()
    bq_manager = BigQueryManagerExtended(project_id=project_id)
    set_bq_manager(bq_manager)
    duration_ms = (time.perf_counter() - start) * 1000.0
    record_latency("bq.initialize", duration_ms, True)
    logger.info("BigQuery manager initialized (extended)")


async def setup_bigquery_schema(project_id: str):
    """Setup complete BigQuery schema (run once during deployment)"""
    manager = BigQueryManagerExtended(project_id=project_id)
    await manager.ensure_dataset_exists()
    await manager.create_tables_if_not_exist()
    logger.info("BigQuery schema setup complete")

    # Tentar também criar tabela de embeddings de memória (nova) após tabelas principais
    try:
        await manager.ensure_memory_embeddings_table()
    except Exception as e:
        logger.error(f"Failed to ensure memory_embeddings table: {e}", exc_info=True)


# =========================
# NOVAS FUNÇÕES PARA EMBEDDINGS DE MEMÓRIA
# =========================

    
class MemoryEmbeddingTableError(Exception):
    pass

def _memory_embeddings_table_ref(project_id: str, dataset_id: str = "eixa") -> str:
    return f"{project_id}.{dataset_id}.memory_embeddings"

async def _table_exists(client: bigquery.Client, table_id: str) -> bool:
    try:
        await asyncio.to_thread(client.get_table, table_id)
        return True
    except Exception:
        return False

def _build_memory_embeddings_table(schema_variant: str, full_table_id: str) -> bigquery.Table:
    # schema_variant: 'vector' ou 'array'
    if schema_variant == 'vector':
        # Usa novo tipo VECTOR<768>. Pode falhar em projetos sem feature habilitada.
        schema = [
            bigquery.SchemaField("user_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("memory_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("content", "STRING"),
            bigquery.SchemaField("input", "STRING"),
            bigquery.SchemaField("output", "STRING"),
            bigquery.SchemaField("language", "STRING"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
            bigquery.SchemaField("embedding", "VECTOR", mode="REQUIRED", description="Embedding vetorial (dim=768)")
        ]
    else:
        # Fallback ARRAY<FLOAT64>
        schema = [
            bigquery.SchemaField("user_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("memory_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("content", "STRING"),
            bigquery.SchemaField("input", "STRING"),
            bigquery.SchemaField("output", "STRING"),
            bigquery.SchemaField("language", "STRING"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
            bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED", description="Embedding como ARRAY<FLOAT64> (dim=768)")
        ]
    table = bigquery.Table(full_table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field="created_at")
    table.clustering_fields = ["user_id"]
    table.description = "Memórias vetoriais (embeddings) do usuário para recuperação semântica"
    return table

class BigQueryManagerExtended(BigQueryManager):  # type: ignore
    async def ensure_memory_embeddings_table(self):  # type: ignore
        """Cria tabela de embeddings de memória se não existir. Tenta VECTOR e faz fallback para ARRAY."""
        full_table_id = _memory_embeddings_table_ref(self.project_id, self.dataset_id)
        if await _table_exists(self.client, full_table_id):
            logger.info("memory_embeddings table already exists")
            return

        # Tenta primeiro VECTOR
        try:
            table_vector = _build_memory_embeddings_table('vector', full_table_id)
            await asyncio.to_thread(self.client.create_table, table_vector)
            logger.info("memory_embeddings table created with VECTOR type")
        except Exception as e:
            logger.warning(f"VECTOR type creation failed ({e}). Falling back to ARRAY<FLOAT64>.")
            table_array = _build_memory_embeddings_table('array', full_table_id)
            await asyncio.to_thread(self.client.create_table, table_array, exists_ok=True)
            logger.info("memory_embeddings table created with ARRAY<FLOAT64> type")

    async def log_memory_embedding(
        self,
        user_id: str,
        memory_id: str,
        content: str,
        input_text: str,
        output_text: str,
        language: str,
        embedding: List[float]
    ) -> None:
        """Insere uma memória vetorial na tabela memory_embeddings."""
        table_id = _memory_embeddings_table_ref(self.project_id, self.dataset_id)
        row = {
            "user_id": user_id,
            "memory_id": memory_id,
            "content": content,
            "input": input_text,
            "output": output_text,
            "language": language,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "embedding": embedding,
        }
        try:
            errors = await asyncio.to_thread(self.client.insert_rows_json, table_id, [row], retry=retry.Retry(deadline=30))
            if errors:
                logger.error(f"BigQuery insert errors (memory_embeddings): {errors}")
            else:
                logger.debug(f"Memory embedding logged for memory_id={memory_id} user_id={user_id}")
        except Exception as e:
            logger.error(f"Error logging memory embedding: {e}", exc_info=True)

    async def search_memory_embeddings(
        self,
        user_id: str,
        query_embedding: List[float],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Busca memórias mais similares via BigQuery usando cosseno (ARRAY<FLOAT64>) ou VECTOR."""
        table_id = _memory_embeddings_table_ref(self.project_id, self.dataset_id)
        # Detectar se tabela usa VECTOR (checando schema) – simples: obter table e ver tipo do campo embedding
        try:
            table = await asyncio.to_thread(self.client.get_table, table_id)
        except Exception as e:
            logger.error(f"Cannot get memory_embeddings table: {e}", exc_info=True)
            return []

        embedding_field = None
        for f in table.schema:
            if f.name == 'embedding':
                embedding_field = f
                break

        if embedding_field is None:
            logger.error("embedding field not found in memory_embeddings table schema")
            return []

        is_vector = embedding_field.field_type.upper() == 'VECTOR'

        if is_vector:
            # Query usando VECTOR_DISTANCE
            query = f"""
            DECLARE query_vec VECTOR<768>;
            SET query_vec = (@query_embedding);
            SELECT memory_id, content, input, output, language, created_at,
                   VECTOR_DISTANCE(embedding, query_vec) AS distance
            FROM `{table_id}`
            WHERE user_id = @user_id
            ORDER BY distance ASC
            LIMIT @top_k
            """
            query_params = [
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ArrayQueryParameter("query_embedding", "FLOAT64", query_embedding),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
            ]
        else:
            # ARRAY<FLOAT64> – calcular cosseno manual
            query = f"""
            SELECT memory_id, content, input, output, language, created_at,
                (
                  (SELECT SUM(a*b) FROM UNNEST(embedding) a WITH OFFSET i JOIN UNNEST(@query_embedding) b WITH OFFSET j ON i=j)
                ) /
                (
                  SQRT((SELECT SUM(a*a) FROM UNNEST(embedding) a)) *
                  SQRT((SELECT SUM(b*b) FROM UNNEST(@query_embedding) b))
                ) AS cosine_similarity
            FROM `{table_id}`
            WHERE user_id = @user_id
            ORDER BY cosine_similarity DESC
            LIMIT @top_k
            """
            query_params = [
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ArrayQueryParameter("query_embedding", "FLOAT64", query_embedding),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
            ]

        job_config = bigquery.QueryJobConfig(query_parameters=query_params)
        try:
            query_job = await asyncio.to_thread(self.client.query, query, job_config=job_config)
            results = await asyncio.to_thread(query_job.result)
            enriched = []
            for row in results:
                enriched.append({
                    "memory_id": row.memory_id,
                    "content": row.content,
                    "input": row.input,
                    "output": row.output,
                    "language": row.language,
                    "created_at": row.created_at.isoformat() if hasattr(row.created_at, 'isoformat') else str(row.created_at),
                    "distance": row.distance if is_vector else (1 - (row.cosine_similarity or 0.0)),
                    "similarity": None if is_vector else (row.cosine_similarity or 0.0)
                })
            return enriched
        except Exception as e:
            logger.error(f"Error searching memory embeddings: {e}", exc_info=True)
            return []
