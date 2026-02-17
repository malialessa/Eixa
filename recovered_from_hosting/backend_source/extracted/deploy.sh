#!/bin/bash

# Script de deploy para EIXA Backend no Google Cloud Run
# Autor: EIXA Team
# Data: 2025-12-21

set -e

echo "🚀 Iniciando deploy do EIXA Backend..."

# Cores para output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configurações
PROJECT_ID="${GCP_PROJECT:-arquitetodadivulgacao}"
REGION="${REGION:-us-east1}"
SERVICE_NAME="eixa-api"

echo -e "${YELLOW}Configurações:${NC}"
echo "  Project: $PROJECT_ID"
echo "  Region: $REGION"
echo "  Service: $SERVICE_NAME"
echo ""

# Verificar se está logado no gcloud
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" &>/dev/null; then
    echo -e "${RED}❌ Você não está autenticado no gcloud${NC}"
    echo "Execute: gcloud auth login"
    exit 1
fi

# Confirmar projeto
echo -e "${YELLOW}Configurando projeto...${NC}"
gcloud config set project $PROJECT_ID

# Build e Deploy
echo -e "${GREEN}📦 Construindo e fazendo deploy...${NC}"
gcloud run deploy $SERVICE_NAME \
    --source . \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars "GCP_PROJECT=$PROJECT_ID,REGION=$REGION,FIRESTORE_DATABASE_ID=(default)" \
    --memory 2Gi \
    --cpu 2 \
    --timeout 300 \
    --max-instances 10 \
    --min-instances 0

# Obter URL do serviço
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region $REGION --format="value(status.url)")

echo ""
echo -e "${GREEN}✅ Deploy concluído com sucesso!${NC}"
echo ""
echo -e "${YELLOW}Próximos passos:${NC}"
echo "1. Configure as variáveis de ambiente faltantes (GEMINI_API_KEY, GOOGLE_CLIENT_ID, etc.):"
echo "   gcloud run services update $SERVICE_NAME --region $REGION --set-env-vars \"GEMINI_API_KEY=sua-key,...\""
echo ""
echo "2. URL do serviço: $SERVICE_URL"
echo ""
echo "3. Verificar logs:"
echo "   gcloud run services logs read $SERVICE_NAME --region $REGION --limit 50"
echo ""
echo "4. Testar health check:"
echo "   curl $SERVICE_URL/"
echo ""

exit 0
