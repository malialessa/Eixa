# EIXA - Assistente de IA Pessoal

EIXA é uma assistente de IA pessoal inteligente que ajuda no gerenciamento de projetos, tarefas, agenda e integração com Google Calendar.

## 🏗️ Arquitetura

```
EIXA/
├── backend/           # API Flask no Google Cloud Run
│   ├── main.py
│   ├── eixa_orchestrator.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── frontend/          # Interface web no Firebase Hosting
│   ├── public/
│   │   └── index.html
│   └── firebase.json
│
└── README.md         # Este arquivo
```

## 🚀 Deploy Rápido

### Backend (Cloud Run)

```bash
cd backend
gcloud run deploy eixa-api \
  --source . \
  --region us-east1 \
  --platform managed \
  --allow-unauthenticated \
  --service-account eixa-cloud-run@arquitetodadivulgacao.iam.gserviceaccount.com \
  --set-env-vars "GCP_PROJECT=arquitetodadivulgacao,REGION=us-east1,GEMINI_API_KEY=YOUR_KEY,GOOGLE_CLIENT_ID=YOUR_ID,GOOGLE_CLIENT_SECRET=YOUR_SECRET,GOOGLE_REDIRECT_URI=https://eixa-api-760851989407.us-east1.run.app/oauth2callback,FRONTEND_URL=https://eixa.web.app,FIRESTORE_DATABASE_ID=eixa" \
  --timeout 300 \
  --memory 1Gi \
  --cpu 2 \
  --project=arquitetodadivulgacao
```

### Frontend (Firebase Hosting)

```bash
cd frontend
firebase login
firebase use arquitetodadivulgacao
firebase deploy --only hosting
```

## 🔗 URLs em Produção

- **Frontend**: https://eixa.web.app
- **API Backend**: https://eixa-api-760851989407.us-east1.run.app
- **OAuth Callback**: https://eixa-api-760851989407.us-east1.run.app/oauth2callback

## 🛠️ Tecnologias

### Backend
- **Python 3.11** - Linguagem principal
- **Flask** - Framework web
- **Google Cloud Run** - Hospedagem serverless
- **Firestore** - Banco de dados NoSQL
- **Vertex AI (Gemini)** - Modelos de IA
- **Google Calendar API** - Integração de calendário

### Frontend
- **HTML/CSS/JavaScript** - Interface web (SPA)
- **Firebase Hosting** - Hospedagem estática
- **Firebase Auth** - Autenticação
- **Firebase SDK 8.x** - Integração

## 📊 Status do Deploy

✅ Backend: Deployed no Cloud Run  
✅ Firestore: Database `eixa` configurado  
✅ APIs: Habilitadas  
✅ Service Account: Configurado com permissões  
✅ OAuth: Configurado  
⏳ Frontend: Pronto para deploy  

## 👤 Autora

Projeto EIXA - Assistente de IA Pessoal
