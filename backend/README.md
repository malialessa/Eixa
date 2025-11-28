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
- `requirements.txt` - Dependências Python
- `Dockerfile` - Configuração do container

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
