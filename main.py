import os
import json
import logging
import time
import asyncio
import functions_framework
from flask import Flask, request, jsonify, Response, redirect # Removido url_for, pois o redirect será um URL completo
from flask_cors import CORS

# === Google OAuth Imports ===
# Estas bibliotecas já são importadas e usadas DENTRO de google_calendar_utils.py
# Não precisamos delas diretamente aqui em main.py se a lógica foi abstraída.
# from google_auth_oauthlib.flow import Flow
# from google.oauth2.credentials import Credentials
# from google.auth.transport.requests import Request
# Removido firestore para DELETE_FIELD, pois google_calendar_utils já o importa quando necessário.
# from google.cloud import firestore
# === END Google OAuth Imports ===

# === EIXA Logic Imports ===
from eixa_orchestrator import orchestrate_eixa_response
from crud_orchestrator import orchestrate_crud_action
# Importe apenas o EIXA_GOOGLE_AUTH_COLLECTION se for estritamente necessário aqui,
# mas geralmente é usado apenas dentro de google_calendar_utils.py
from config import GEMINI_TEXT_MODEL, GEMINI_VISION_MODEL # Removido EIXA_GOOGLE_AUTH_COLLECTION
# Agora main.py só precisa importar GoogleCalendarUtils. A instância dela cuidará de tudo.
from google_calendar_utils import GoogleCalendarUtils

# === Inicialização do Logger ===
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Instância do Flask ===
app = Flask(__name__)
# Habilita CORS para todas as rotas. Em produção, considere restringir Access-Control-Allow-Origin
# para os domínios específicos do seu frontend.
CORS(app)

# === Carregamento e Validação de variáveis do ambiente ===
# Definido como None inicialmente, serão populadas de forma mais segura.
GCP_PROJECT = None
REGION = None
GEMINI_API_KEY = None
GOOGLE_CLIENT_ID = None
GOOGLE_CLIENT_SECRET = None
GOOGLE_REDIRECT_URI = None
FRONTEND_URL = None

# === Instância do Google Calendar Utils ===
# Inicializada de forma lazy para garantir que variáveis de ambiente estejam disponíveis.
google_calendar_utils_instance = None

def _initialize_app_globals():
    """
    Inicializa variáveis globais e instâncias de classes que dependem de variáveis de ambiente.
    Chamado uma vez quando o worker da aplicação inicia.
    """
    global GCP_PROJECT, REGION, GEMINI_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, FRONTEND_URL, google_calendar_utils_instance

    GCP_PROJECT = os.environ.get("GCP_PROJECT")
    REGION = os.environ.get("REGION", "us-central1")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI")
    FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") # Default para ambiente de dev local

    if not GCP_PROJECT:
        logger.critical("Variável de ambiente 'GCP_PROJECT' não definida. A aplicação pode não funcionar.")
        # Em Cloud Run, variáveis de ambiente ausentes no startup levam a falhas de inicialização do container.
        # Aqui, apenas logamos, mas o container pode reiniciar.
    if not GEMINI_API_KEY:
        logger.warning("Variável de ambiente 'GEMINI_API_KEY' não definida. Interações com LLM podem falhar.")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI or not FRONTEND_URL: # Adicionado FRONTEND_URL à validação
        logger.warning("Uma ou mais variáveis de ambiente do Google OAuth (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, FRONTEND_URL) não estão definidas. A integração com o Google Calendar pode não funcionar corretamente.")
    else:
        # Inicializa a instância de GoogleCalendarUtils apenas se as variáveis OAuth estiverem presentes
        google_calendar_utils_instance = GoogleCalendarUtils()

    logger.info(f"Variáveis de ambiente carregadas. GCP Project: {GCP_PROJECT}, Region: {REGION}")
    logger.info(f"Google OAuth Config: Client ID present: {bool(GOOGLE_CLIENT_ID)}, Redirect URI present: {bool(GOOGLE_REDIRECT_URI)}, Frontend URL present: {bool(FRONTEND_URL)}")
    logger.info(f"Google Calendar Utils instance ready: {google_calendar_utils_instance is not None}")

# === Chamada para inicializar globais na inicialização do worker ===
_initialize_app_globals()

@app.before_request
def log_request_info():
    """Registra informações básicas de cada requisição HTTP recebida."""
    if request.method != 'OPTIONS':
        logger.debug(json.dumps({
            "event": "http_request_received",
            "method": request.method,
            "path": request.path,
            "remote_addr": request.remote_addr,
            "headers_snippet": {k: v for k, v in request.headers.items() if k.lower() in ['user-agent', 'x-forwarded-for', 'x-cloud-trace-context']}
        }))

# === Rota de Health Check (GET para a raiz) ===
@app.route("/", methods=["GET"])
def root_check():
    """Endpoint simples para verificar se a aplicação está no ar."""
    logger.debug("Health check requested.")
    return jsonify({"status": "ok", "message": "EIXA está no ar. Use /interact para interagir."}), 200

# === NOVA ROTA: Iniciar o fluxo de autenticação do Google Calendar ===
@app.route("/auth/google", methods=["GET"])
async def google_auth():
    """
    Inicia o fluxo OAuth 2.0 para o Google Calendar.
    O frontend deve chamar este endpoint com o `user_id` como query parameter.
    """
    user_id = request.args.get('user_id')
    if not user_id:
        logger.error("/auth/google: Missing user_id for OAuth initiation.")
        return jsonify({"status": "error", "message": "Parâmetro 'user_id' é obrigatório para iniciar a autenticação Google."}), 400

    if google_calendar_utils_instance is None or not google_calendar_utils_instance.oauth_config_ready:
        logger.critical("/auth/google: Google OAuth environment variables are not properly set or GoogleCalendarUtils not initialized.")
        return jsonify({"status": "error", "message": "Erro de configuração do servidor para autenticação Google. Contate o suporte."}), 500

    try:
        # Delega a geração da URL de autorização para GoogleCalendarUtils
        authorization_url = await google_calendar_utils_instance.get_auth_url(user_id=user_id)
        
        if authorization_url:
            logger.info(f"/auth/google: Generated authorization URL for user {user_id}. Returning URL.")
            # O frontend DEVE redirecionar o usuário para esta URL.
            # A URL já inclui o 'state' necessário.
            return jsonify({"auth_url": authorization_url}), 200
        else:
            logger.error(f"/auth/google: Failed to generate authorization URL for user {user_id}. check GoogleCalendarUtils logs for details.")
            return jsonify({"status": "error", "message": "Não foi possível gerar a URL de autenticação Google."}), 500
    except Exception as e:
        logger.critical(f"/auth/google: Unexpected error during OAuth URL generation for user {user_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Erro interno ao preparar autenticação Google."}), 500

# === NOVA ROTA: Callback para o Google OAuth ===
@app.route("/oauth2callback", methods=["GET"])
async def oauth2callback():
    """
    Recebe o redirecionamento do Google após a autorização do usuário.
    Delega o processamento do callback para GoogleCalendarUtils.
    Redireciona o usuário de volta para o frontend.
    """
    # capture a URL completa da requisição, que contém todos os parâmetros (code, state, error)
    authorization_response_url = request.url 
    logger.info(f"/oauth2callback: Received callback. Full URL: {authorization_response_url}")

    if google_calendar_utils_instance is None or not google_calendar_utils_instance.oauth_config_ready:
        logger.critical("/oauth2callback: GoogleCalendarUtils not initialized or OAuth config not ready.")
        # Redireciona para o frontend com erro de configuração
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Erro%20de%20configuração%20do%20servidor")

    try:
        # Delega todo o processamento do callback para GoogleCalendarUtils
        # Ele validará o state, trocará o code por tokens, e salvará.
        result = await google_calendar_utils_instance.handle_oauth2_callback(authorization_response_url)
        
        user_id_from_callback = result.get("user_id") # Pega o user_id que foi extraído/validado no utils
        
        if result.get("status") == "success":
            logger.info(f"/oauth2callback: Successfully processed Google Calendar credentials for user: {user_id_from_callback}")
            # Redireciona o usuário de volta para o frontend com um status de sucesso.
            # É importante passar o user_id de volta para o frontend se ele precisar.
            return redirect(f"{FRONTEND_URL}/dashboard?auth_status=success&message=Google%20Calendar%20conectado%20com%20sucesso&user_id={user_id_from_callback or ''}")
        else:
            logger.error(f"/oauth2callback: handle_oauth2_callback failed for user {user_id_from_callback}: {result.get('message')}")
            # Redireciona o usuário de volta para o frontend com um status de erro.
            return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Falha%20ao%20conectar%20Google%20Calendar&user_id={user_id_from_callback or ''}")

    except Exception as e:
        logger.critical(f"/oauth2callback: Critical error during OAuth callback processing: {e}", exc_info=True)
        # Em caso de erro crítico inesperado, redireciona para o frontend com uma mensagem genérica
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Falha%20crítica%20ao%20conectar%20Google%20Calendar")

# === Rota principal da API (POST e OPTIONS para /interact) ===
@app.route("/interact", methods=["POST", "OPTIONS"])
async def interact_api():
    """
    Ponto de entrada principal para todas as interações da EIXA (chat, CRUD, visualizações).
    """
    start_time = time.time()
    logger.debug("interact_api: Function started.")

    headers = {
        'Access-Control-Allow-Origin': FRONTEND_URL, # Restringir Access-Control-Allow-Origin para o FRONTEND_URL em produção!
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '3600'
    }
    
    # Se o FRONTEND_URL não estiver definido, mantenha '*' para desenvolvimento local
    if not FRONTEND_URL:
        headers['Access-Control-Allow-Origin'] = '*'
        logger.warning("FRONTEND_URL não definido, usando Access-Control-Allow-Origin: '*' para CORS.")


    if request.method == 'OPTIONS':
        logger.debug("interact_api: OPTIONS request received.")
        return Response(status=204, headers=headers)

    request_json = request.get_json(silent=True)
    if not request_json:
        logger.error("interact_api: Invalid request body or empty JSON.")
        return jsonify({"status": "error", "response": "Corpo da requisição inválido ou JSON vazio."}), 400, headers

    user_id = request_json.get('user_id')
    request_type = request_json.get('request_type')
    debug_mode = request_json.get('debug_mode', False)

    logger.debug(f"interact_api: Received user_id='{user_id}', request_type='{request_type}', debug_mode='{debug_mode}'.")

    if not user_id or not isinstance(user_id, str):
        logger.error(f"interact_api: Missing or invalid user_id: '{user_id}'.")
        return jsonify({"status": "error", "response": "O campo 'user_id' é obrigatório e deve ser uma string."}), 400, headers

    if not request_type:
        logger.error(f"interact_api: Missing request_type.")
        return jsonify({"status": "error", "response": "O campo 'request_type' é obrigatório."}), 400, headers

    # --- Verificação de dependências globais ---
    if not GCP_PROJECT:
        logger.critical("GCP_PROJECT não definido. A aplicação não pode operar.")
        return jsonify({"status": "error", "response": "Erro de configuração do servidor (GCP_PROJECT ausente)."}), 500, headers
    if not GEMINI_API_KEY:
        logger.critical("GEMINI_API_KEY não definido. Interações com LLM não são possíveis.")
        return jsonify({"status": "error", "response": "Erro de configuração do servidor (Chave Gemini ausente)."}), 500, headers
    
    # O eixa_orchestrator já lida com o caso de GoogleCalendarUtils não estar inicializado ou configurado.
    # Não precisamos de uma verificação adicional aqui que retorne 500.

    try:
        if request_type in ['chat_and_view', 'view_data']:
            logger.debug(f"interact_api: Calling orchestrate_eixa_response for request_type: {request_type}")
            response_payload = await orchestrate_eixa_response(
                user_id=user_id,
                user_message=request_json.get('message'),
                uploaded_file_data=request_json.get('uploaded_file_data'),
                view_request=request_json.get('view_request'),
                gcp_project_id=GCP_PROJECT,
                region=REGION,
                gemini_api_key=GEMINI_API_KEY,
                gemini_text_model=GEMINI_TEXT_MODEL,
                gemini_vision_model=GEMINI_VISION_MODEL,
                firestore_collection_interactions='interactions',
                debug_mode=debug_mode
            )
            # NOVO: Se o orchestrator retornou uma URL de redirecionamento OAuth, passe-a.
            if response_payload.get("google_auth_redirect_url"):
                logger.info(f"interact_api: Orchestrator returned Google OAuth redirect URL for user {user_id}. Returning to frontend.")
                return jsonify(response_payload), 200, headers # Retorna o payload com a URL para o frontend

        elif request_type == 'crud_action':
            logger.debug(f"interact_api: Calling orchestrate_crud_action for request_type: {request_type}. Payload: {request_json}")
            response_payload = await orchestrate_crud_action(request_json)
        else:
            logger.error(f"interact_api: Invalid request type received: '{request_type}'.")
            return jsonify({"status": "error", "response": f"Tipo de requisição inválido: '{request_type}'."}), 400, headers

        duration = time.time() - start_time
        logger.info(json.dumps({
            "event": "request_completed",
            "user_id": user_id,
            "request_type": request_type,
            "duration_seconds": f"{duration:.2f}",
            "response_status": response_payload.get("status", "unknown"),
        }))
        logger.debug(f"interact_api: Response payload being sent: {response_payload}")

        return jsonify(response_payload), 200, headers

    except Exception as e:
        duration = time.time() - start_time
        logger.critical(json.dumps({
            "event": "orchestration_failed",
            "user_id": user_id,
            "request_type": request_type,
            "duration_seconds": f"{duration:.2f}",
            "error_type": type(e).__name__,
            "error_message": str(e)
        }), exc_info=True)

        return jsonify({
            "status": "error",
            "response": "Erro interno inesperado.",
            "debug_info": [f"Erro interno: {type(e).__name__} - {str(e)}"]
        }), 500, headers

# === Função de entrada do functions_framework para o Cloud Run ===
@functions_framework.http
def eixa_entry(request):
    """
    Função de entrada para o Google Cloud Run/Functions.
    Ela encapsula a aplicação Flask e lida com o ciclo de vida da requisição.
    """
    logger.debug("eixa_entry: Request received by functions_framework wrapper.")
    # Flask é uma aplicação WSGI. functions_framework.http pode encapsular
    # diretamente a instância Flask. As rotas assíncronas são manipuladas
    # pela configuração subjacente do Cloud Run (Gunicorn com workers assíncronos).
    return app(request.environ, lambda status, headers: [])

# === Execução local para testes ===
if __name__ == '__main__':
    # Configura variáveis de ambiente para o ambiente de desenvolvimento local.
    # Estas devem ser as mesmas que você configurará no Cloud Run.
    os.environ["GCP_PROJECT"] = os.environ.get("GCP_PROJECT", "arquitetodadivulgacao")
    os.environ["REGION"] = os.environ.get("REGION", "us-east1")
    os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "YOUR_LOCAL_GEMINI_API_KEY_HERE")
    os.environ["FIRESTORE_DATABASE_ID"] = os.environ.get("FIRESTORE_DATABASE_ID", "(default)")
    
    # === SUAS CREDENCIAIS DE TESTE PARA OAUTH LOCALLY ===
    # Você precisará criar um OAuth 2.0 Client ID do tipo "Aplicativo da Web" no GCP
    # para 'http://localhost:8080/oauth2callback' como Redirect URI.
    os.environ["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID_LOCAL")
    os.environ["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "YOUR_GOOGLE_CLIENT_SECRET_LOCAL")
    os.environ["GOOGLE_REDIRECT_URI"] = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8080/oauth2callback")
    os.environ["FRONTEND_URL"] = os.environ.get("FRONTEND_URL", "http://localhost:5173")
    
    # Inicializa as variáveis globais APENAS uma vez ao rodar o arquivo diretamente
    _initialize_app_globals()

    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Iniciando localmente na porta {port}")
    app.run(debug=True, host='0.0.0.0', port=port)