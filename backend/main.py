import os
import json
import logging
import time
import asyncio
import functions_framework
from flask import Flask, request, jsonify, Response, redirect 

from flask_cors import CORS

from eixa_orchestrator import orchestrate_eixa_response
from crud_orchestrator import orchestrate_crud_action
from config import GEMINI_TEXT_MODEL, GEMINI_VISION_MODEL
from google_calendar_utils import GoogleCalendarUtils

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

GCP_PROJECT = None
REGION = None
GEMINI_API_KEY = None
GOOGLE_CLIENT_ID = None
GOOGLE_CLIENT_SECRET = None
GOOGLE_REDIRECT_URI = None
FRONTEND_URL = None

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
    FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

    if not GCP_PROJECT:
        logger.critical("Variável de ambiente 'GCP_PROJECT' não definida. A aplicação pode não funcionar.")
    if not GEMINI_API_KEY:
        logger.warning("Variável de ambiente 'GEMINI_API_KEY' não definida. Interações com LLM podem falhar.")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI or not FRONTEND_URL:
        logger.warning("Uma ou mais variáveis de ambiente do Google OAuth (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, FRONTEND_URL) não estão definidas. A integração com o Google Calendar pode não funcionar corretamente.")
    else:
        google_calendar_utils_instance = GoogleCalendarUtils()

    logger.info(f"Variáveis de ambiente carregadas. GCP Project: {GCP_PROJECT}, Region: {REGION}")
    logger.info(f"Google OAuth Config: Client ID present: {bool(GOOGLE_CLIENT_ID)}, Redirect URI present: {bool(GOOGLE_REDIRECT_URI)}, Frontend URL present: {bool(FRONTEND_URL)}")
    logger.info(f"Google Calendar Utils instance ready: {google_calendar_utils_instance is not None}")

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

@app.route("/", methods=["GET"])
def root_check():
    """Endpoint simples para verificar se a aplicação está no ar."""
    logger.debug("Health check requested.")
    return jsonify({"status": "ok", "message": "EIXA está no ar. Use /interact para interagir."}), 200

# === ROTA: Iniciar o fluxo de autenticação do Google Calendar (AGORA NÃO USADA PELO CHAT, APENAS POR BOTÃO) ===
# Esta rota pode ser usada pelo frontend para iniciar o fluxo OAuth.
# O `eixa_orchestrator` também pode retornar uma `google_auth_redirect_url` em seu payload.
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
        authorization_url = await google_calendar_utils_instance.get_auth_url(user_id=user_id)
        
        if authorization_url:
            logger.info(f"/auth/google: Generated authorization URL for user {user_id}. Returning URL.")
            return jsonify({"auth_url": authorization_url}), 200
        else:
            logger.error(f"/auth/google: Failed to generate authorization URL for user {user_id}. check GoogleCalendarUtils logs for details.")
            return jsonify({"status": "error", "message": "Não foi possível gerar a URL de autenticação Google."}), 500
    except Exception as e:
        logger.critical(f"/auth/google: Unexpected error during OAuth URL generation for user {user_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Erro interno ao preparar autenticação Google."}), 500

# === ROTA: Callback para o Google OAuth ===
@app.route("/oauth2callback", methods=["GET"])
async def oauth2callback():
    """
    Recebe o redirecionamento do Google após a autorização do usuário.
    Delega o processamento do callback para GoogleCalendarUtils.
    Redireciona o usuário de volta para o frontend.
    """
    authorization_response_url = request.url 
    logger.info(f"/oauth2callback: Received callback. Full URL: {authorization_response_url}")

    if google_calendar_utils_instance is None or not google_calendar_utils_instance.oauth_config_ready:
        logger.critical("/oauth2callback: GoogleCalendarUtils not initialized or OAuth config not ready.")
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Erro%20de%20configuração%20do%20servidor")

    try:
        result = await google_calendar_utils_instance.handle_oauth2_callback(authorization_response_url)
        
        user_id_from_callback = result.get("user_id") 
        
        if result.get("status") == "success":
            logger.info(f"/oauth2callback: Successfully processed Google Calendar credentials for user: {user_id_from_callback}")
            return redirect(f"{FRONTEND_URL}/dashboard?auth_status=success&message=Google%20Calendar%20conectado%20com%20sucesso&user_id={user_id_from_callback or ''}")
        else:
            logger.error(f"/oauth2callback: handle_oauth2_callback failed for user {user_id_from_callback}: {result.get('message')}")
            return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Falha%20ao%20conectar%20Google%20Calendar&user_id={user_id_from_callback or ''}")

    except Exception as e:
        logger.critical(f"/oauth2callback: Critical error during OAuth callback processing: {e}", exc_info=True)
        return redirect(f"{FRONTEND_URL}/dashboard?auth_status=error&message=Falha%20crítica%20ao%20conectar%20Google%20Calendar")

# === Rota principal da API (POST e OPTIONS para /interact) ===
@app.route("/interact", methods=["POST", "OPTIONS"])
async def interact_api():
    """
    Ponto de entrada principal para todas as interações da EIXA (chat, CRUD, visualizações, etc.).
    """
    start_time = time.time()
    logger.debug("interact_api: Function started.")

    headers = {
        'Access-Control-Allow-Origin': FRONTEND_URL,
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '3600'
    }
    
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
    request_type = request_json.get('request_type', 'chat_and_view') # Default para 'chat_and_view'
    debug_mode = request_json.get('debug_mode', False)

    logger.debug(f"interact_api: Received user_id='{user_id}', request_type='{request_type}', debug_mode='{debug_mode}'.")

    if not user_id or not isinstance(user_id, str):
        logger.error(f"interact_api: Missing or invalid user_id: '{user_id}'.")
        return jsonify({"status": "error", "response": "O campo 'user_id' é obrigatório e deve ser uma string."}), 400, headers

    if not GCP_PROJECT:
        logger.critical("GCP_PROJECT não definido. A aplicação não pode operar.")
        return jsonify({"status": "error", "response": "Erro de configuração do servidor (GCP_PROJECT ausente)."}), 500, headers
    if not GEMINI_API_KEY:
        logger.critical("GEMINI_API_KEY não definido. Interações com LLM não são possíveis.")
        return jsonify({"status": "error", "response": "Erro de configuração do servidor (Chave Gemini ausente)."}), 500, headers
    
    try:
        # TODA a lógica principal foi movida para orchestrate_eixa_response
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
            debug_mode=debug_mode,
            request_type=request_type, # Passa o request_type
            action=request_json.get('action'), # Passa a ação para request_type=google_calendar_action
            action_data=request_json.get('data') # Passa os dados para request_type=google_calendar_action
        )
        
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

@functions_framework.http
def eixa_entry(request):
    """
    Função de entrada para o Google Cloud Run/Functions.
    """
    logger.debug("eixa_entry: Request received by functions_framework wrapper.")
    return app(request.environ, lambda status, headers: [])

if __name__ == '__main__':
    os.environ["GCP_PROJECT"] = os.environ.get("GCP_PROJECT", "arquitetodadivulgacao")
    os.environ["REGION"] = os.environ.get("REGION", "us-east1")
    os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "YOUR_LOCAL_GEMINI_API_KEY_HERE")
    os.environ["FIRESTORE_DATABASE_ID"] = os.environ.get("FIRESTORE_DATABASE_ID", "(default)")
    
    os.environ["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID_LOCAL")
    os.environ["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "YOUR_GOOGLE_CLIENT_SECRET_LOCAL")
    os.environ["GOOGLE_REDIRECT_URI"] = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8080/oauth2callback")
    os.environ["FRONTEND_URL"] = os.environ.get("FRONTEND_URL", "http://localhost:5173")
    
    _initialize_app_globals()

    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Iniciando localmente na porta {port}")
    app.run(debug=True, host='0.0.0.0', port=port)