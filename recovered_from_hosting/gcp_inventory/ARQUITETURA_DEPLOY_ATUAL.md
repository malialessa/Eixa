# Arquitetura de Deploy Atual — EIXA

Gerado em: 2026-02-13
Projeto GCP: `arquitetodadivulgacao`
Conta autenticada: `amaliaalessa@gmail.com`

## 1) Frontend (Hosting)
- Domínio ativo observado: `https://eixa.web.app`
- O frontend recuperado aponta para backend em Cloud Run:
  - `https://eixa-api-760851989407.us-east1.run.app/interact`
  - `https://eixa-api-760851989407.us-east1.run.app/actions`
  - `https://eixa-api-760851989407.us-east1.run.app/auth/google`
- Firebase config embutido indica projeto: `arquitetodadivulgacao`.

## 2) Backend (Cloud Run)

### Serviços EIXA (us-east1)

#### a) eixa-api
- URL: `https://eixa-api-h2527f4cna-ue.a.run.app`
- Revisão ativa: `eixa-api-00044-f4g` (100% tráfego)
- Imagem ativa:
  - `us-east1-docker.pkg.dev/arquitetodadivulgacao/cloud-run-source-deploy/eixa-api@sha256:563e2d1a073723367546b0eabf24c549b742489189a3ba1bbd7e8e9764a91003`
- Service Account: `eixa-cloud-run@arquitetodadivulgacao.iam.gserviceaccount.com`
- Recursos: CPU `2`, Memória `2Gi`, porta `8080`, timeout `300s`, concurrency `160`
- Env vars:
  - `GCP_PROJECT=arquitetodadivulgacao`
  - `REGION=us-east1`
  - `FIRESTORE_DATABASE=eixa`
  - `FRONTEND_URL=https://eixa.web.app`
  - `REDIS_URL=redis://10.117.92.11:6379`

#### b) eixa-backend
- URL: `https://eixa-backend-h2527f4cna-ue.a.run.app`
- Revisão ativa: `eixa-backend-00004-5kn` (100% tráfego)
- Imagem ativa:
  - `us-east1-docker.pkg.dev/arquitetodadivulgacao/cloud-run-source-deploy/eixa-backend@sha256:950fb0ff3c2fbf1fe4c453391a66c0d3a82118c5d86cfd8d2316552f6b72f312`
- Service Account: `760851989407-compute@developer.gserviceaccount.com`
- Recursos: CPU `1000m`, Memória `512Mi`, porta `8080`, timeout `300s`, concurrency `80`
- Env vars:
  - `GCP_PROJECT=arquitetodadivulgacao`
  - `REGION=us-east1`
  - `FRONTEND_URL=https://eixa.web.app`
  - `FIRESTORE_DATABASE_ID=eixa`

#### c) eixa
- URL: `https://eixa-h2527f4cna-ue.a.run.app`
- Revisão ativa: `eixa-00070-7q2` (100% tráfego)
- Imagem ativa:
  - `gcr.io/arquitetodadivulgacao/eixa:latest`
- Service Account: `eixa-cloud-run@arquitetodadivulgacao.iam.gserviceaccount.com`
- Recursos: CPU `4000m`, Memória `2Gi`, porta `8080`, timeout `300s`, concurrency `80`
- Env vars:
  - `GCP_PROJECT=arquitetodadivulgacao`
  - `FRONTEND_URL=https://eixa.web.app`

## 3) Cloud Functions
- Listagem retornou **0 funções** no projeto.
- Conclusão: operação principal está em **Cloud Run**, não em Cloud Functions.

## 4) Dados e Armazenamento

### Firestore
Bancos encontrados:
- `(default)` em `nam5`
- `eixa` em `us-east1`
- `jobapplications` em `nam5`

### BigQuery
- Dataset encontrado: `eixa`

### Artifact Registry
- Repositório: `cloud-run-source-deploy` (formato DOCKER, us-east1)

### Cloud Storage (buckets relevantes)
- `eixa-files`
- `arquitetodadivulgacao_cloudbuild`
- `run-sources-arquitetodadivulgacao-us-east1`
- Além de buckets de outros apps do mesmo projeto (`arquitetodaalma`, `job-resumes1`, etc.)

### Secrets Manager
- Total de secrets encontrados: `0`

### Redis / Memorystore
- Nenhuma instância encontrada nas regiões verificadas (`us-east1`, `us-central1`, `southamerica-east1`).
- Apesar disso, `eixa-api` usa `REDIS_URL=redis://10.117.92.11:6379`.

## 5) Observações de Arquitetura
- O projeto `arquitetodadivulgacao` hospeda múltiplos sistemas além do EIXA (vários serviços em `us-central1` e `us-east1`).
- O backend EIXA está ativo em `us-east1`, com imagens no Artifact Registry de source deploy.
- Há indício de cache Redis em IP privado sem recurso Memorystore visível no mesmo projeto/regiões consultadas (pode ser recurso externo, outro projeto, ou endpoint legado).
- O frontend recuperado referencia um endpoint `run.app` com identificador por número do projeto (`...760851989407...`), enquanto a listagem do Cloud Run mostra URL canônica `...h2527f4cna-ue.a.run.app`; ambos podem coexistir conforme roteamento/domínio e histórico de deploy.

## 6) Artefatos recuperados do último deploy (`eixa-api-00044-f4g`)

### Imagem Docker (cópia local)
- Origem (Artifact Registry):
  - `us-east1-docker.pkg.dev/arquitetodadivulgacao/cloud-run-source-deploy/eixa-api@sha256:563e2d1a073723367546b0eabf24c549b742489189a3ba1bbd7e8e9764a91003`
- Cópia exportada no repositório:
  - `recovered_from_hosting/backend_image/eixa-api_00044-f4g_sha256-563e2d1a.tar.gz`
- Tamanho aproximado:
  - `261 MiB` (274119226 bytes)

### Source bundle do build (código usado no deploy)
- Origem (Cloud Storage):
  - `gs://run-sources-arquitetodadivulgacao-us-east1/services/eixa-api/1766351922.818424-a53e3804c5e647ef9936078cd714efb7.zip`
- Cópia local:
  - `recovered_from_hosting/backend_source/eixa-api_last-deploy_source.zip`
- Extraído em:
  - `recovered_from_hosting/backend_source/extracted/`

### Comandos úteis de restore
- Carregar imagem exportada localmente:
  - `gunzip -c recovered_from_hosting/backend_image/eixa-api_00044-f4g_sha256-563e2d1a.tar.gz | docker load`
- Rodar container localmente (teste rápido):
  - `docker run --rm -p 8080:8080 us-east1-docker.pkg.dev/arquitetodadivulgacao/cloud-run-source-deploy/eixa-api@sha256:563e2d1a073723367546b0eabf24c549b742489189a3ba1bbd7e8e9764a91003`
