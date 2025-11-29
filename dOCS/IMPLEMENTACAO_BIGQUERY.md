# 🚀 Implementação BigQuery - Guia Completo

## ✅ Arquivos Criados

1. **`backend/bigquery_utils.py`** (355 linhas)
   - `BigQueryManager`: Classe completa para gerenciar BigQuery
   - Métodos: `log_interaction()`, `log_task()`, `query_user_analytics()`, etc.
   - Suporte a particionamento e clustering para performance
   
2. **`backend/setup_bigquery.py`** (30 linhas)
   - Script para inicializar dataset e tabelas
   - Execute uma vez antes do deploy

3. **Atualizações**:
   - ✅ `main.py`: Importa e inicializa BigQuery no startup
   - ✅ `eixa_orchestrator.py`: Importa bq_manager e loga interações
   - ✅ `requirements.txt`: Adicionado `google-cloud-bigquery>=3.11.0`

---

## 📋 Passos para Implementação

### **Passo 1: Setup do BigQuery (Uma vez)**

Execute o script de setup para criar dataset e tabelas:

```bash
cd /workspaces/Eixa/backend
python setup_bigquery.py
```

**O que isso faz:**
- Cria dataset `eixa` em `us-east1`
- Cria 5 tabelas:
  - `user_interactions` (particionada por timestamp, clustering user_id+intent)
  - `tasks` (particionada por created_at, clustering user_id)
  - `emotional_memories`
  - `projects`
  - `routines`

**Saída esperada:**
```
INFO - Starting BigQuery setup for project: arquitetodadivulgacao
INFO - Dataset eixa ready
INFO - Table user_interactions ready
INFO - Table tasks ready
INFO - Table emotional_memories ready
INFO - Table projects ready
INFO - Table routines ready
INFO - ✅ BigQuery setup complete!
```

### **Passo 2: Deploy para Cloud Run**

```bash
cd /workspaces/Eixa/backend

# Build da imagem
gcloud builds submit --tag gcr.io/arquitetodadivulgacao/eixa:latest

# Deploy com permissões BigQuery
gcloud run deploy eixa \
  --image gcr.io/arquitetodadivulgacao/eixa:latest \
  --region us-east1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars GCP_PROJECT=arquitetodadivulgacao \
  --service-account eixa-cloud-run@arquitetodadivulgacao.iam.gserviceaccount.com
```

**Importante:** Garanta que a service account tenha permissões:
```bash
gcloud projects add-iam-policy-binding arquitetodadivulgacao \
  --member="serviceAccount:eixa-cloud-run@arquitetodadivulgacao.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding arquitetodadivulgacao \
  --member="serviceAccount:eixa-cloud-run@arquitetodadivulgacao.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"
```

### **Passo 3: Testar Logging**

Após deploy, faça uma interação no frontend e verifique:

```bash
# Verificar logs do Cloud Run
gcloud run logs read eixa --region us-east1 --limit 50 | grep BigQuery

# Verificar dados no BigQuery
bq query --use_legacy_sql=false \
  'SELECT COUNT(*) as total_interactions FROM `arquitetodadivulgacao.eixa.user_interactions`'
```

**Resultado esperado:**
- Logs: `"BigQuery logging scheduled for interaction <uuid>"`
- Query retorna contagem > 0

---

## 🔍 Como Funciona

### **1. Inicialização (main.py)**

No startup do Cloud Run:
```python
from bigquery_utils import initialize_bigquery, bq_manager

def _initialize_app_globals():
    # ... carrega env vars ...
    
    if GCP_PROJECT:
        initialize_bigquery(GCP_PROJECT)  # Cria global bq_manager
        logger.info("BigQuery initialized successfully")
```

### **2. Logging Automático (eixa_orchestrator.py)**

Toda interação é logada automaticamente:
```python
from bigquery_utils import bq_manager

async def orchestrate_eixa_response(...):
    # ... processamento normal ...
    
    # No final, antes do return:
    if bq_manager and user_message:
        asyncio.create_task(
            bq_manager.log_interaction(
                user_id=user_id,
                interaction_id=str(uuid.uuid4()),
                message_in=user_message,
                message_out=response_payload["response"],
                intent=detected_intent,
                model_used="gemini-2.5-flash"
            )
        )
```

**Vantagem:** Non-blocking - logging acontece em background sem atrasar resposta

### **3. Queries de Analytics**

Exemplo para obter métricas de usuário:

```python
analytics = await bq_manager.query_user_analytics(user_id="123", days=30)

# Retorna:
{
    "total_interactions": 145,
    "active_days": 18,
    "avg_duration_ms": 1823.5,
    "total_tokens": 43200,
    "error_count": 2,
    "top_intents": [
        {"value": "add_task", "count": 52},
        {"value": "chat", "count": 38},
        {"value": "view_agenda", "count": 25}
    ]
}
```

---

## 📊 Tabelas e Schemas

### **user_interactions** (Particionada)
```sql
user_id: STRING
interaction_id: STRING
timestamp: TIMESTAMP (partition key)
message_in: STRING
message_out: STRING
intent: STRING (clustering key)
language: STRING
duration_ms: INT64
model_used: STRING
tokens_in: INT64
tokens_out: INT64
error_code: STRING
```

**Uso:** Analytics de uso, debugging, RAG de histórico

### **tasks**
```sql
user_id: STRING (clustering key)
task_id: STRING
description: STRING
date: DATE
time: TIME
duration_minutes: INT64
completed: BOOL
origin: STRING (user_added, routine, google_calendar)
routine_item_id: STRING
google_calendar_event_id: STRING
created_at: TIMESTAMP (partition key)
updated_at: TIMESTAMP
```

**Uso:** Analytics de produtividade, completion rates, padrões de tarefas

### **emotional_memories**
```sql
user_id: STRING
memory_id: STRING
description: STRING
emotional_valence: FLOAT64
trigger: STRING
context: STRING
timestamp: TIMESTAMP
```

**Uso:** RAG emocional, padrões de comportamento, checkpoint emocional

---

## 🎯 Próximos Passos

### **Integração com CRUD**
Adicionar logging de tasks em `crud_orchestrator.py`:

```python
from bigquery_utils import bq_manager

async def orchestrate_crud_action(operation, collection, user_id, data):
    # ... lógica CRUD existente ...
    
    # Após criar/atualizar task:
    if collection == "agenda" and bq_manager:
        await bq_manager.log_task(user_id, task_data)
```

### **Dashboard de Analytics**
Criar endpoint `/api/analytics` para exibir métricas:

```python
@app.route("/analytics", methods=["GET"])
async def get_analytics():
    user_id = request.args.get("user_id")
    analytics = await bq_manager.query_user_analytics(user_id, days=30)
    completion_rate = await bq_manager.get_task_completion_rate(user_id, days=7)
    
    return jsonify({
        "analytics": analytics,
        "completion_rate_7d": completion_rate
    })
```

### **RAG com BigQuery**
Para contexto histórico rico:

```python
query = """
SELECT message_in, message_out, timestamp
FROM `arquitetodadivulgacao.eixa.user_interactions`
WHERE user_id = @user_id
  AND intent = 'chat'
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
ORDER BY timestamp DESC
LIMIT 20
"""
# Use isso para construir contexto sem carregar todo histórico do Firestore
```

---

## 💰 Impacto de Custo

**BigQuery Pricing:**
- Storage: $0.02/GB/mês (primeiros 10GB grátis)
- Queries: $5/TB processado (primeiro 1TB/mês grátis)
- Streaming Inserts: $0.05/200MB

**Estimativa para 10.000 interações/mês:**
- Storage: ~0.5GB = **$0.01/mês**
- Streaming: ~50MB = **$0.01/mês**
- Queries (analytics): ~10MB/query, 100 queries = **$0.00** (dentro do free tier)

**Total adicional: ~$0.02/mês** (quase grátis!)

---

## 🔧 Troubleshooting

### Erro: "Permission denied on dataset eixa"
```bash
# Adicionar permissões BigQuery à service account
gcloud projects add-iam-policy-binding arquitetodadivulgacao \
  --member="serviceAccount:eixa-cloud-run@arquitetodadivulgacao.iam.gserviceaccount.com" \
  --role="roles/bigquery.admin"
```

### Erro: "Dataset not found"
```bash
# Executar setup novamente
python setup_bigquery.py
```

### Dados não aparecem no BigQuery
```bash
# Verificar logs do Cloud Run
gcloud run logs read eixa --region us-east1 --limit 100 | grep -i bigquery

# Buscar erros:
gcloud run logs read eixa --region us-east1 --limit 100 | grep -i error
```

### Query lenta
```sql
-- Verificar partitioning está funcionando (deve usar < 1GB)
SELECT COUNT(*)
FROM `arquitetodadivulgacao.eixa.user_interactions`
WHERE user_id = "123"
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)

-- Check de performance no console BigQuery
```

---

## ✅ Checklist de Implementação

- [ ] Executar `python setup_bigquery.py` (dataset + tabelas)
- [ ] Adicionar permissões BigQuery à service account
- [ ] Deploy do backend atualizado para Cloud Run
- [ ] Testar interação no frontend
- [ ] Verificar logs: `"BigQuery logging scheduled"`
- [ ] Query: `SELECT COUNT(*) FROM user_interactions` > 0
- [ ] (Opcional) Integrar logging de tasks em `crud_orchestrator.py`
- [ ] (Opcional) Criar endpoint `/analytics` para métricas
- [ ] (Opcional) Configurar BigQuery para RAG de histórico

---

## 📚 Recursos Adicionais

- [BigQuery Python Client](https://cloud.google.com/python/docs/reference/bigquery/latest)
- [Partitioning in BigQuery](https://cloud.google.com/bigquery/docs/partitioned-tables)
- [Clustering in BigQuery](https://cloud.google.com/bigquery/docs/clustered-tables)
- [Streaming Inserts](https://cloud.google.com/bigquery/docs/streaming-data-into-bigquery)

---

**Status:** ✅ Código pronto para deploy | ⚙️ Setup pendente | 🚀 Deploy pendente
