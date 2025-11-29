# Guia de Manutenção EIXA

## 📋 Checklist Pós-Deploy

- [x] Backend deployado no Cloud Run
- [x] Frontend deployado no Firebase Hosting
- [x] Firestore configurado
- [x] OAuth configurado
- [x] Service Account com permissões
- [x] Estrutura de pastas reorganizada
- [x] Documentação atualizada

## 🔄 Como Atualizar o Backend

```bash
cd backend
# Faça suas alterações nos arquivos .py
gcloud run deploy eixa-api \
  --source . \
  --region us-east1 \
  --project=arquitetodadivulgacao
```

## 🔄 Como Atualizar o Frontend

```bash
cd frontend
# Edite public/index.html
firebase deploy --only hosting
```

## 🔧 Atualizar Variáveis de Ambiente

```bash
cd backend
gcloud run services update eixa-api \
  --region us-east1 \
  --update-env-vars "NOVA_VARIAVEL=valor" \
  --project=arquitetodadivulgacao
```

## 📊 Ver Logs

### Backend (Cloud Run)
```bash
gcloud run services logs tail eixa-api --region us-east1
```

### Frontend (Firebase Hosting)
Acesse: https://console.firebase.google.com/project/arquitetodadivulgacao/hosting

## 🔐 Rotação de Credenciais

### 1. Atualizar OAuth Credentials
1. Acesse: https://console.cloud.google.com/apis/credentials
2. Edite o Client ID OAuth
3. Gere novas credenciais se necessário
4. Atualize as variáveis de ambiente no Cloud Run

### 2. Atualizar Gemini API Key
```bash
gcloud run services update eixa-api \
  --region us-east1 \
  --update-env-vars "GEMINI_API_KEY=nova_chave" \
  --project=arquitetodadivulgacao
```

## 🐛 Debug Comum

### Frontend não conecta ao Backend
1. Verifique se a URL está correta em `frontend/public/index.html`:
   ```javascript
   CLOUD_FUNCTION_URL: 'https://eixa-api-760851989407.us-east1.run.app/interact'
   ```
2. Verifique CORS no backend (`main.py`)
3. Verifique se o backend está rodando: `curl https://eixa-api-760851989407.us-east1.run.app/`

### Erro de autenticação Firebase
1. Verifique `firebaseConfig` em `index.html`
2. Verifique permissões no Console Firebase
3. Limpe cache do navegador

### Erro 500 no Backend
1. Veja os logs: `gcloud run services logs tail eixa-api --region us-east1`
2. Verifique variáveis de ambiente
3. Verifique permissões do Service Account

## 📈 Monitoramento

### Métricas Cloud Run
```bash
gcloud run services describe eixa-api \
  --region us-east1 \
  --project=arquitetodadivulgacao
```

### Uso do Firebase Hosting
Acesse: https://console.firebase.google.com/project/arquitetodadivulgacao/hosting/usage

## 💰 Custo Estimado

| Serviço | Custo Mensal Estimado |
|---------|----------------------|
| Cloud Run | $10-30 |
| Firestore | $5-20 |
| Vertex AI (Gemini) | $20-50 |
| Firebase Hosting | Grátis |
| **Total** | **$35-100** |

## 🔒 Segurança

### Regras do Firestore
Atualize em: https://console.firebase.google.com/project/arquitetodadivulgacao/firestore/rules

### Auditoria de Permissões
```bash
gcloud projects get-iam-policy arquitetodadivulgacao
```

## 📞 Suporte

- **Console GCP**: https://console.cloud.google.com/
- **Console Firebase**: https://console.firebase.google.com/
- **Cloud Run**: https://console.cloud.google.com/run
- **Firestore**: https://console.firebase.google.com/project/arquitetodadivulgacao/firestore

## 🚀 URLs de Produção

- **Frontend**: https://eixa.web.app
- **Backend API**: https://eixa-api-760851989407.us-east1.run.app
- **OAuth Callback**: https://eixa-api-760851989407.us-east1.run.app/oauth2callback
