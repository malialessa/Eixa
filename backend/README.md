# EIXA Backend

API Flask hospedada no Google Cloud Run que fornece toda a lógica de processamento, IA e integração com serviços.

## 🚀 Deploy

```bash
cd backend
gcloud run deploy eixa-api \
  --source . \
  --region us-east1 \
  --platform managed \
  --allow-unauthenticated \
  --service-account eixa-cloud-run@arquitetodadivulgacao.iam.gserviceaccount.com \
  --set-env-vars "GCP_PROJECT=arquitetodadivulgacao,REGION=us-east1,GEMINI_API_KEY=YOUR_KEY,GOOGLE_CLIENT_ID=YOUR_CLIENT_ID,GOOGLE_CLIENT_SECRET=YOUR_SECRET,GOOGLE_REDIRECT_URI=https://eixa-api-760851989407.us-east1.run.app/oauth2callback,FRONTEND_URL=https://eixa.web.app,FIRESTORE_DATABASE_ID=eixa" \
  --timeout 300 \
  --memory 1Gi \
  --cpu 2 \
  --project=arquitetodadivulgacao
```

## 📂 Estrutura

- `main.py` - Ponto de entrada da API Flask
- `eixa_orchestrator.py` - Orquestrador principal das respostas da IA
- `crud_orchestrator.py` - Operações CRUD
- `firestore_*.py` - Utilitários do Firestore
- `google_calendar_utils.py` - Integração com Google Calendar
- `vertex_utils.py` - Integração com Vertex AI/Gemini
- `bigquery_utils.py` - Utilitários do BigQuery para analytics e RAG
- `metrics_utils.py` - Coleta de métricas de performance
- `requirements.txt` - Dependências Python
- `Dockerfile` - Configuração do container
- `tests/` - Testes unitários e de integração

## 🔧 Variáveis de Ambiente

- `GCP_PROJECT` - ID do projeto GCP
- `REGION` - Região do Cloud Run
- `GEMINI_API_KEY` - Chave da API Gemini
- `GOOGLE_CLIENT_ID` - OAuth Client ID
- `GOOGLE_CLIENT_SECRET` - OAuth Client Secret
- `GOOGLE_REDIRECT_URI` - URL de callback OAuth
- `FRONTEND_URL` - URL do frontend
- `FIRESTORE_DATABASE_ID` - Nome do banco Firestore (default: eixa)

## 🔗 URL da API

Produção: `https://eixa-api-760851989407.us-east1.run.app`

## 📊 Métricas e Observabilidade

O sistema agora coleta métricas de performance para operações críticas e as armazena no BigQuery na tabela `operation_metrics`.

As métricas coletadas incluem:
- **Latência**: Duração de chamadas a APIs externas (Gemini) e funções internas (busca vetorial, etc.).
- **Sucesso/Falha**: Registro do resultado de operações críticas.
- **Contagem**: Número de itens retornados em buscas.

As métricas são aplicadas usando o decorador `@measure_async("nome.operacao")` do `metrics_utils.py`.

## ✅ Testes

O projeto agora inclui uma suíte de testes automatizados usando `pytest`.

### Instalar dependências de desenvolvimento:
```bash
pip install -r requirements-dev.txt
```

### Rodar todos os testes:
```bash
python -m pytest
```

### Rodar um arquivo de teste específico:
```bash
python -m pytest tests/test_vectorstore_cache.py
```
