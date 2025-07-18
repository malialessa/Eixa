import os
import json
import logging
import time
import asyncio
import functions_framework
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

# === Inicialização do Logger ===
# ALTERADO PARA DEBUG PARA VER TODOS OS LOGS
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Instância do Flask ===
app = Flask(__name__)
CORS(app)

# === Carregamento de variáveis do ambiente ===
GCP_PROJECT = os.environ.get("GCP_PROJECT")
REGION = os.environ.get("REGION", "us-central1")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not GCP_PROJECT:
    logger.critical("Variável de ambiente 'GCP_PROJECT' não definida.")
    raise EnvironmentError("GCP_PROJECT environment variable is not set.")

logger.info(f"Aplicação EIXA inicializada. GCP Project: {GCP_PROJECT}, Region: {REGION}")

# === Imports da lógica de negócio ===
from eixa_orchestrator import orchestrate_eixa_response
from crud_orchestrator import orchestrate_crud_action
from config import GEMINI_TEXT_MODEL, GEMINI_VISION_MODEL # Importe diretamente do config

@app.before_request
def log_request_info():
    if request.method != 'OPTIONS':
        logger.debug(json.dumps({ # Alterado para debug
            "event": "http_request_received",
            "method": request.method,
            "path": request.path,
            "remote_addr": request.remote_addr,
            "headers_snippet": {k: v for k, v in request.headers.items() if k.lower() in ['user-agent', 'x-forwarded-for', 'x-cloud-trace-context']}
        }))

# === Rota de Health Check (GET para a raiz) ===
@app.route("/", methods=["GET"])
def root_check():
    logger.debug("Health check requested.") # Adicionado log
    return jsonify({"status": "ok", "message": "EIXA está no ar. Use /interact para interagir."}), 200

# === Rota principal da API (POST e OPTIONS para /interact) ===
@app.route("/interact", methods=["POST", "OPTIONS"])
async def interact_api():
    start_time = time.time()
    logger.debug("interact_api: Function started.") # Adicionado log

    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '3600'
    }

    if request.method == 'OPTIONS':
        logger.debug("interact_api: OPTIONS request received.") # Adicionado log
        return Response(status=204, headers=headers)

    request_json = request.get_json(silent=True)
    if not request_json:
        logger.error("interact_api: Invalid request body or empty JSON.") # Adicionado log
        return jsonify({"status": "error", "response": "Corpo da requisição inválido ou JSON vazio."}), 400, headers

    user_id = request_json.get('user_id')
    request_type = request_json.get('request_type')
    debug_mode = request_json.get('debug_mode', False)

    logger.debug(f"interact_api: Received user_id='{user_id}', request_type='{request_type}', debug_mode='{debug_mode}'.") # Adicionado log

    if not user_id or not isinstance(user_id, str):
        logger.error(f"interact_api: Missing or invalid user_id: '{user_id}'.") # Adicionado log
        return jsonify({"status": "error", "response": "O campo 'user_id' é obrigatório e deve ser uma string."}), 400, headers

    if not request_type:
        logger.error(f"interact_api: Missing request_type.") # Adicionado log
        return jsonify({"status": "error", "response": "O campo 'request_type' é obrigatório."}), 400, headers

    try:
        if request_type in ['chat_and_view', 'view_data']:
            logger.debug(f"interact_api: Calling orchestrate_eixa_response for request_type: {request_type}") # Adicionado log
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
        elif request_type == 'crud_action':
            logger.debug(f"interact_api: Calling orchestrate_crud_action for request_type: {request_type}. Payload: {request_json}") # Adicionado log
            response_payload = await orchestrate_crud_action(request_json)
        else:
            logger.error(f"interact_api: Invalid request type received: '{request_type}'.") # Adicionado log
            return jsonify({"status": "error", "response": f"Tipo de requisição inválido: '{request_type}'."}), 400, headers

        duration = time.time() - start_time
        logger.info(json.dumps({
            "event": "request_completed",
            "user_id": user_id,
            "request_type": request_type,
            "duration_seconds": f"{duration:.2f}",
            "response_status": response_payload.get("status", "unknown"),
        }))
        logger.debug(f"interact_api: Response payload being sent: {response_payload}") # Adicionado log

        return jsonify(response_payload), 200, headers

    except Exception as e:
        duration = time.time() - start_time
        logger.critical(json.dumps({ # Alterado para critical
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
    logger.debug("eixa_entry: Request received by functions_framework wrapper.") # Adicionado log
    return app(request.environ, lambda status, headers: [])

# === Execução local para testes ===
if __name__ == '__main__':
    os.environ["GCP_PROJECT"] = os.environ.get("GCP_PROJECT", "local-dev-project")
    os.environ["REGION"] = os.environ.get("REGION", "us-central1")
    os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "YOUR_LOCAL_GEMINI_API_KEY") 
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Iniciando localmente na porta {port}")
    app.run(debug=True, host='0.0.0.0', port=port)