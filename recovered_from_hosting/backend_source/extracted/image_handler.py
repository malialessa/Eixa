# --- image_handler.py ---
"""
Handler para upload de imagens para Google Cloud Storage.
Usado para armazenar imagens enviadas pelo usuário no chat e avatares de perfil.
"""

import logging
import uuid
import base64
from datetime import timedelta
from typing import Dict, Any, Optional
from google.cloud import storage

logger = logging.getLogger(__name__)

# Nome do bucket no GCS (deve existir no projeto GCP)
GCS_BUCKET_NAME = "eixa-files"
GCS_IMAGES_FOLDER = "images"
GCS_AVATARS_FOLDER = "avatars"

def _get_storage_client() -> storage.Client:
    """
    Retorna um cliente do Google Cloud Storage.
    Usa as credenciais padrão do ambiente (Application Default Credentials).
    """
    try:
        client = storage.Client()
        logger.debug(f"IMAGE_HANDLER | Storage client initialized successfully.")
        return client
    except Exception as e:
        logger.error(f"IMAGE_HANDLER | Error initializing Storage client: {e}", exc_info=True)
        raise

async def upload_image_to_gcs(
    user_id: str,
    image_data: str,
    filename: str = None,
    folder: str = GCS_IMAGES_FOLDER
) -> Optional[str]:
    """
    Faz upload de uma imagem em base64 para o Google Cloud Storage.
    
    Args:
        user_id: ID do usuário (usado para organizar arquivos)
        image_data: String base64 da imagem (pode incluir prefixo 'data:image/...')
        filename: Nome do arquivo (opcional, gera UUID se ausente)
        folder: Pasta no bucket (padrão: 'images')
    
    Returns:
        URL pública assinada da imagem (válida por 7 dias) ou None em caso de erro
    """
    try:
        # Remove prefixo 'data:image/...;base64,' se presente
        if ',' in image_data and image_data.startswith('data:'):
            image_data = image_data.split(',', 1)[1]
        
        # Decodifica base64
        image_bytes = base64.b64decode(image_data)
        
        # Gera nome do arquivo se não fornecido
        if not filename:
            file_extension = "png"  # padrão
            filename = f"{uuid.uuid4()}.{file_extension}"
        
        # Caminho completo no bucket: folder/user_id/filename
        blob_path = f"{folder}/{user_id}/{filename}"
        
        # Upload para GCS
        client = _get_storage_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_path)
        
        # Define content-type baseado na extensão
        content_type = "image/png"
        if filename.endswith(".jpg") or filename.endswith(".jpeg"):
            content_type = "image/jpeg"
        elif filename.endswith(".gif"):
            content_type = "image/gif"
        elif filename.endswith(".webp"):
            content_type = "image/webp"
        
        blob.upload_from_string(image_bytes, content_type=content_type)
        logger.info(f"IMAGE_HANDLER | Image uploaded to GCS: {blob_path}")
        
        # Torna o objeto público e retorna URL pública (fallback sem assinatura)
        # Observação: requer que o bucket permita objetos públicos; ajuste a política conforme necessário.
        try:
            blob.make_public()
            public_url = blob.public_url
            logger.info(f"IMAGE_HANDLER | Public URL generated for user '{user_id}': {blob_path}")
            return public_url
        except Exception:
            # Se não conseguir tornar público, tenta URL assinada (ambiente com chave privada)
            signed_url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(days=7),
                method="GET"
            )
            logger.info(f"IMAGE_HANDLER | Signed URL generated for user '{user_id}': {blob_path}")
            return signed_url
        
    except Exception as e:
        logger.error(f"IMAGE_HANDLER | Error uploading image for user '{user_id}': {e}", exc_info=True)
        return None

async def upload_avatar_to_gcs(
    user_id: str,
    avatar_data: str,
    filename: str = None
) -> Optional[str]:
    """
    Faz upload de um avatar de perfil para o Google Cloud Storage.
    Wrapper de upload_image_to_gcs com pasta específica para avatares.
    
    Args:
        user_id: ID do usuário
        avatar_data: String base64 do avatar
        filename: Nome do arquivo (opcional, gera UUID se ausente)
    
    Returns:
        URL pública assinada do avatar ou None em caso de erro
    """
    return await upload_image_to_gcs(
        user_id=user_id,
        image_data=avatar_data,
        filename=filename or f"avatar_{uuid.uuid4()}.png",
        folder=GCS_AVATARS_FOLDER
    )

def delete_image_from_gcs(blob_path: str) -> bool:
    """
    Deleta uma imagem do Google Cloud Storage.
    
    Args:
        blob_path: Caminho completo do blob no bucket (ex: 'images/user123/file.png')
    
    Returns:
        True se deletado com sucesso, False caso contrário
    """
    try:
        client = _get_storage_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_path)
        blob.delete()
        logger.info(f"IMAGE_HANDLER | Image deleted from GCS: {blob_path}")
        return True
    except Exception as e:
        logger.error(f"IMAGE_HANDLER | Error deleting image from GCS ({blob_path}): {e}", exc_info=True)
        return False

# --- END OF FILE image_handler.py ---
