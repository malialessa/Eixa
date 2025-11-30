# EIXA v3.0 - Checklist de Testes Completo

Base API (Cloud Run): https://eixa-api-760851989407.us-east1.run.app
Base Frontend (Firebase Hosting): [INSIRA_URL_HOSTING_SE_AINDA_NAO_COLOCADA_AQUI]

Use um `user_id` de teste dedicado (ex: `qa_tester`) para isolar dados.

## 0. Pré-Requisitos
1. Ter chave/credenciais: (se necessário) confirmar que chamadas públicas `/actions` e `/interact` funcionam sem headers adicionais.
2. Criar `user_id` novo nas chamadas para ambiente limpo.
3. (Opcional) Ativar modo debug no perfil se disponível (via chat: "ativar modo debug").
4. Ter um arquivo de imagem local (ex: `teste.png`) para upload.

## 1. CRUD de Tarefas (API)
1. Criar tarefa:
```
curl -X POST $API/actions -H "Content-Type: application/json" -d '{"user_id":"qa_tester","item_type":"task","action":"create","data":{"description":"Planejar sprint","date":"2025-12-01","time":"09:00"}}'
```
Esperado: status success + task_id.
2. Atualizar descrição e hora:
```
curl -X POST $API/actions -H "Content-Type: application/json" -d '{"user_id":"qa_tester","item_type":"task","action":"update","item_id":"<TASK_ID>","data":{"date":"2025-12-01","description":"Planejar sprint detalhado","time":"10:30"}}'
```
3. Concluir:
```
curl -X POST $API/actions -H "Content-Type: application/json" -d '{"user_id":"qa_tester","item_type":"task","action":"update","item_id":"<TASK_ID>","data":{"date":"2025-12-01","completed":true}}'
```
4. Deletar:
```
curl -X POST $API/actions -H "Content-Type: application/json" -d '{"user_id":"qa_tester","item_type":"task","action":"delete","item_id":"<TASK_ID>","data":{"date":"2025-12-01"}}'
```

## 2. Fallback de Data/Hora (Chat)
Mensagem sem data/hora: "Adicionar tarefa revisar backlog" para `/interact`.
Esperado: resposta pedindo confirmação já com data=hoje e time=00:00.
Confirmar com "sim".

## 3. Vinculação Automática a Projeto
1. Criar projeto:
```
curl -X POST $API/actions -H "Content-Type: application/json" -d '{"user_id":"qa_tester","item_type":"project","action":"create","data":{"name":"Projeto Alpha","description":"Escopo inicial"}}'
```
2. Chat: "Adicionar tarefa preparar kickoff Projeto Alpha amanhã às 10h".
Esperado: confirmação com referência ao projeto (debug: `project_id`).

## 4. Status Kanban via Chat
1. Criar tarefa (API) "Mover teste" hoje.
2. Chat: "Mover tarefa 'Mover teste' para em andamento" => status in_progress.
3. Chat: "Marcar tarefa 'Mover teste' como concluída" => status done + completed true.

## 5. Bulk Delete
1. Criar três tarefas com prefixo "limparx" (datas iguais).
2. Filtro:
```
curl -X POST $API/actions -H "Content-Type: application/json" -d '{"user_id":"qa_tester","item_type":"task","action":"bulk_delete","data":{"description_contains":"limparx"}}'
```
Esperado: "X tarefas excluídas." sem erro de data.
3. Lista explícita: criar duas tarefas e passar seus IDs/datas no array.

## 6. Rotinas
1. Chat: "Crie uma rotina matinal: 08:00 alongar 10 minutos, 08:15 café 15 minutos, repetir diariamente." => confirmação.
2. Confirmar "sim".
3. Chat: "Aplicar minha Rotina Matinal amanhã" => tarefas adicionadas no dia seguinte.
4. Chat: "Aplicar minha Rotina Matinal" (sem data) => fallback hoje.

## 7. Multi-Account Google Calendar
1. GET contas:
```
curl -X GET "$API/calendar/accounts?user_id=qa_tester"
```
2. Iniciar OAuth no navegador:
`$API/auth/google?user_id=qa_tester&account_label=Trabalho`
3. Após callback, listar novamente contas.
4. Adicionar segunda conta `Pessoal`.
5. Selecionar ativa:
```
curl -X POST $API/calendar/accounts/select -H "Content-Type: application/json" -d '{"user_id":"qa_tester","account_id":"<ACCOUNT_ID>"}'
```

## 8. Upload
```
curl -X POST $API/upload -F file=@teste.png -F user_id=qa_tester
```
Esperado: JSON com `public_url` acessível em navegador.

## 9. Fluxo de Confirmação
1. Criar tarefa via chat => resposta aguardando "sim".
2. Mandar "não" => cancelamento.
3. Mensagem ambígua ("talvez") em estado de confirmação => repete mensagem de confirmação.

## 10. Validações de Erro
1. Hora inválida:
```
curl -X POST $API/actions -H "Content-Type: application/json" -d '{"user_id":"qa_tester","item_type":"task","action":"create","data":{"description":"Erro","date":"2025-12-02","time":"25:61"}}'
```
Esperado: mensagem de formato inválido.
2. Update tarefa inexistente.
3. Projeto update com campo proibido: enviar `{"foo":"bar"}`.
4. Rotina apply nome inexistente.

## 11. Firestore Consistência
1. Verificar `last_active` muda em cada /interact.
2. Deletar última tarefa de um dia => documento do dia removido.

## 12. Observabilidade
1. Executar 5 chamadas `/interact` rápidas e checar ausência de 500.
2. Confirmar que bulk_delete não gera log com "A data é obrigatória".

## 13. Segurança Básica
1. Omite `user_id` => erro campos obrigatórios.
2. Ação desconhecida => erro ação não reconhecida.
3. Injeção HTML na descrição => armazenado como texto.

## 14. BigQuery (se permissões ajustadas)
1. Chamar `/interact` e checar ausência de 403 em logs.
2. Se 403 persistir, conceder role dataEditor ao service account do Cloud Run e repetir.

## 15. Critérios de Aceite
- Nenhum 500 em cenários válidos.
- Fallbacks (tarefa e rotina) funcionam.
- Vinculação a projeto ocorre somente quando nome exato aparece.
- Bulk delete por filtro opera sem exigir data.
- Rotina aplicada gera tarefas corretas.
- Multi-account Calendar lista e seleciona contas.
- Upload produz URL acessível.
- Kanban status sincroniza completed/done.
- Confirmação cancela corretamente e reprompt funciona.

## 16. Variáveis de Apoio
Defina para facilitar:
```
API=https://eixa-api-760851989407.us-east1.run.app
USER=qa_tester
```

## 17. Sequência Recomendada
Siga a ordem: CRUD -> Chat fallback -> Projeto -> Kanban -> Bulk -> Rotina -> Calendar -> Upload -> Confirmação -> Erros -> Firestore -> Observabilidade -> Segurança -> BigQuery.

---
Observação: Ajuste a URL do frontend se ainda não registrada aqui. Caso queira extender testes automatizados depois, este checklist serve de base para criar scripts.
