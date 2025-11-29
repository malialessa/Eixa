"""Script de migração de embeddings do Firestore para BigQuery

Uso:
  python migrate_firestore_embeddings_to_bigquery.py

Pré-requisitos:
  - Application Default Credentials com acesso BigQuery + Firestore
  - Tabela memory_embeddings criada (o script tenta criar se não existir)
"""
import asyncio
import logging
import uuid
from google.cloud import firestore
from bigquery_utils import BigQueryManager
from config import TOP_LEVEL_COLLECTIONS_MAP

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("migration")

GCP_PROJECT = None  # opcional se quiser forçar
DATASET_ID = "eixa"

EMBEDDINGS_COLLECTION = TOP_LEVEL_COLLECTIONS_MAP['embeddings']

async def migrate(project_id: str):
    logger.info("Iniciando migração Firestore -> BigQuery (memory embeddings)")
    bq = BigQueryManager(project_id=project_id, dataset_id=DATASET_ID)
    await bq.ensure_dataset_exists()
    await bq.ensure_memory_embeddings_table()

    fs_client = firestore.Client(project=project_id)
    col_ref = fs_client.collection(EMBEDDINGS_COLLECTION)
    docs = list(col_ref.stream())
    logger.info(f"Total de documentos de embeddings encontrados: {len(docs)}")

    migrated = 0
    for doc in docs:
        data = doc.to_dict() or {}
        user_id = data.get('user_id')
        embedding = data.get('embedding')
        content = data.get('content')
        input_text = data.get('input')
        output_text = data.get('output')
        language = data.get('language', 'pt')
        if not (user_id and embedding and isinstance(embedding, list)):
            continue
        memory_id = doc.id or str(uuid.uuid4())
        try:
            await bq.log_memory_embedding(
                user_id=user_id,
                memory_id=memory_id,
                content=content or f"User: {input_text}\nAI: {output_text}",
                input_text=input_text or "",
                output_text=output_text or "",
                language=language,
                embedding=embedding
            )
            migrated += 1
        except Exception as e:
            logger.error(f"Falha ao migrar doc {doc.id}: {e}", exc_info=True)

    logger.info(f"Migração concluída. Registros migrados: {migrated}")

def main():
    import os
    project = GCP_PROJECT or os.getenv("GCP_PROJECT")
    if not project:
        logger.error("GCP_PROJECT não definido em env ou variável GCP_PROJECT.")
        return
    asyncio.run(migrate(project))

if __name__ == "__main__":
    main()
