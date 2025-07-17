import os
import json
import logging
import time
import asyncio # Mantenha o asyncio
import functions_framework
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

# === Inicialização do Logger ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Instância do Flask ===
app = Flask(__name__)
CORS(app)

# === Carregamento de variáveis do ambiente ===
GCP_PROJECT = os.environ.get("GCP_PROJECT")
REGION = os.environ.get("REGION", "us-central1")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Usar os valores do config.py diretamente para os modelos default
# (ou remover se não forem usados aqui no main.py, e sim passados do config para orchestrator)
# GEMINI_TEXT_MODEL e GEMINI_VISION_MODEL serão importados de config.py, então não precisam ser lidos do ambiente aqui
# e o eixa_orchestrator receberá os valores corretos.

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
        logger.info(json.dumps({
            "event": "http_request_received",
            "method": request.method,
            "path": request.path,
            "remote_addr": request.remote_addr,
            "headers_snippet": {k: v for k, v in request.headers.items() if k.lower() in ['user-agent', 'x-forwarded-for', 'x-cloud-trace-context']}
        }))

# === Função principal chamada via Cloud Run ===
# Mude a função para assíncrona
@functions_framework.http
async def eixa_entry(request):
    start_time = time.time()

    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS', # Mantém GET e OPTIONS para a URL base
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '3600'
    }

    if request.method == 'OPTIONS':
        return Response(status=204, headers=headers)

    # Se a requisição for GET para a raiz, pode retornar um status "OK" simples
    if request.method == 'GET' and request.path == '/':
        return jsonify({"status": "ok", "message": "EIXA está no ar."}), 200, headers

    if request.method != 'POST':
        return jsonify({"status": "error", "response": "Método não permitido. Use POST para interações."}), 405, headers

    request_json = request.get_json(silent=True)
    if not request_json:
        return jsonify({"status": "error", "response": "Corpo da requisição inválido ou JSON vazio."}), 400, headers

    user_id = request_json.get('user_id')
    request_type = request_json.get('request_type')
    debug_mode = request_json.get('debug_mode', False)

    if not user_id or not isinstance(user_id, str):
        return jsonify({"status": "error", "response": "O campo 'user_id' é obrigatório e deve ser uma string."}), 400, headers

    if not request_type:
        return jsonify({"status": "error", "response": "O campo 'request_type' é obrigatório."}), 400, headers

    try:
        if request_type in ['chat_and_view', 'view_data']:
            # Await diretamente, sem asyncio.run
            response_payload = await orchestrate_eixa_response(
                user_id=user_id,
                user_message=request_json.get('message'),
                uploaded_file_data=request_json.get('uploaded_file_data'),
                view_request=request_json.get('view_request'),
                gcp_project_id=GCP_PROJECT,
                region=REGION,
                gemini_api_key=GEMINI_API_KEY,
                gemini_text_model=GEMINI_TEXT_MODEL, # Passa do config.py
                gemini_vision_model=GEMINI_VISION_MODEL, # Passa do config.py
                firestore_collection_interactions='interactions',
                debug_mode=debug_mode
            )
        elif request_type == 'crud_action':
            # Await diretamente, sem asyncio.run
            response_payload = await orchestrate_crud_action(request_json)
        else:
            return jsonify({"status": "error", "response": f"Tipo de requisição inválido: '{request_type}'."}), 400, headers

        duration = time.time() - start_time
        logger.info(json.dumps({
            "event": "request_completed",
            "user_id": user_id,
            "request_type": request_type,
            "duration_seconds": f"{duration:.2f}",
            "response_status": response_payload.get("status", "unknown"),
        }))

        return jsonify(response_payload), 200, headers

    except Exception as e:
        duration = time.time() - start_time
        logger.error(json.dumps({
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

# === Execução local para testes ===
if __name__ == '__main__':
    # No ambiente local, para rodar com Flask nativo, você ainda precisa da rota GET
    # para testar o Health Check. No Cloud Run, functions_framework.http já lida com a raiz.
    @app.route("/", methods=["GET"])
    def root_check_local():
        return jsonify({"status": "ok", "message": "EIXA está no ar (modo local)."}), 200

    os.environ["GCP_PROJECT"] = os.environ.get("GCP_PROJECT", "local-dev-project")
    os.environ["REGION"] = os.environ.get("REGION", "us-central1")
    # Defina uma chave Gemini para testes locais se necessário
    os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "YOUR_LOCAL_GEMINI_API_KEY") 
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Iniciando localmente na porta {port}")
    app.run(debug=True, host='0.0.0.0', port=port)