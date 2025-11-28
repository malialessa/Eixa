# EIXA Frontend

Interface web do EIXA hospedada no Firebase Hosting.

## 🚀 Deploy

```bash
cd frontend
firebase login
firebase use arquitetodadivulgacao
firebase deploy --only hosting
```

## 📂 Estrutura

```
frontend/
├── public/
│   ├── index.html      # Aplicação principal (SPA)
│   └── assets/         # Imagens, ícones, etc
├── firebase.json       # Configuração do Firebase Hosting
└── README.md          # Este arquivo
```

## 🔧 Configuração

O arquivo `index.html` contém a configuração da API do backend:

```javascript
const config = {
    CLOUD_FUNCTION_URL: 'https://eixa-api-760851989407.us-east1.run.app/interact',
    firebaseConfig: {
        apiKey: "AIzaSyAKEdwGJFjAXyY3vQUMm0sCIvdSfs-WInw",
        authDomain: "arquitetodadivulgacao.firebaseapp.com",
        projectId: "arquitetodadivulgacao",
        storageBucket: "arquitetodadivulgacao.firebaseapp.com",
        messagingSenderId: "760851989407",
        appId: "1:760851989407:web:485fc9e2d0328b479473aa"
    }
};
```

## 🔗 URLs

- Produção: `https://eixa.web.app` ou `https://eixa.firebaseapp.com`
- API Backend: `https://eixa-api-760851989407.us-east1.run.app`

## ✨ Funcionalidades

- Chat com IA (Gemini)
- Gerenciamento de projetos e tarefas
- Integração com Google Calendar
- Visualização de agenda e rotinas
- Upload de arquivos
- Modo escuro/claro
- Responsivo (mobile e desktop)
