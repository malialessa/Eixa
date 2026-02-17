import logging
from google.cloud import firestore
import os # Importar o módulo os para acessar variáveis de ambiente

logger = logging.getLogger(__name__)

_firestore_client_instance = None

def _initialize_firestore_client_instance():
    """
    Inicializa e retorna uma única instância do cliente Firestore.
    Implementa o padrão Singleton e permite a conexão a um banco de dados específico.
    """
    global _firestore_client_instance
    if _firestore_client_instance is None:
        try:
            # Obtém o ID do projeto e o ID do banco de dados das variáveis de ambiente
            # `GCP_PROJECT` deve estar configurado no Cloud Run
            project_id = os.getenv("GCP_PROJECT") 
            # `FIRESTORE_DATABASE_ID` é a nova variável que você configurou
            database_id = os.getenv("FIRESTORE_DATABASE_ID")

            if not project_id:
                logger.critical("Environment variable 'GCP_PROJECT' not set. Cannot initialize Firestore client.")
                raise ValueError("GCP_PROJECT environment variable is required for Firestore initialization.")

            # Inicializa o cliente Firestore, passando o project e database_id
            _firestore_client_instance = firestore.Client(project=project_id, database=database_id)
            
            # Log para confirmar qual banco de dados está sendo usado
            logger.info(f"Firestore client initialized successfully for project '{project_id}' and database '{database_id if database_id else '(default)'}'.")
        except Exception as e:
            logger.critical(f"Failed to initialize Firestore client: {e}", exc_info=True)
            raise # Re-lança o erro para falhar o startup se o Firestore não puder ser conectado.
    return _firestore_client_instance