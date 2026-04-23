# bug-voice-reporter

App desktop leve para Windows que captura um relato de bug por voz, transcreve com Gemini ou OpenAI, formata em bug report estruturado e copia o resultado para a área de transferência.

O app opera com privacidade por padrão: não salva relatório local, não salva transcrição bruta e não grava log em arquivo a menos que você habilite isso no `.env`.

## Visão Geral

Fluxo principal:

1. Pressione `Ctrl+Tab`
2. Fale livremente o relato
3. Pressione `Ctrl+Tab` novamente para encerrar manualmente
4. Ou aguarde 10 segundos contínuos de silêncio após a primeira fala
5. O áudio é enviado ao provedor configurado para transcrição
6. A transcrição vira um bug report estruturado
7. O texto final é copiado para o clipboard

Atalhos adicionais:

- `Ctrl+Caps Lock`: descarta o áudio atual e reinicia uma nova gravação em 5 segundos
- `Ctrl+Tab` durante a contagem: cancela o reinício agendado

## O Que Ficou No App

- Hotkeys globais
- Notificações visuais em estilo HUD
- Sons de feedback
- Bandeja do sistema com menu simples
- Retry de transcrição
- Validação de formato da resposta
- Clipboard com limpeza automática
- Sem janela de configurações
- Persistência local desativada por padrão
- Suporte a Gemini e OpenAI por configuração

## Requisitos

- Windows 10 ou Windows 11
- Python 3.11+
- Microfone funcional
- Chave do Gemini ou da OpenAI

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Configuração

Copie `.env.example` para `.env` e preencha a chave do provedor que você quer usar:

```env
GEMINI_API_KEY=AIza...
OPENAI_API_KEY=sk-...
```

No `.env`, você escolhe o provedor por etapa:

- `TRANSCRIPTION_PROVIDER=gemini` ou `openai`
- `FORMATTER_PROVIDER=gemini` ou `openai`

Atalhos padrão:

- `APP_HOTKEY=ctrl+tab`
- `APP_RESTART_HOTKEY=ctrl+caps lock`

Flags úteis:

- `SAVE_LAST_OUTPUT=false`: não grava `last_output.txt` por padrão
- `DEBUG_SAVE_TRANSCRIPTION=false`: não grava `last_transcription.txt` por padrão
- `CLIPBOARD_CLEAR_SECONDS=120`: limpa o conteúdo copiado automaticamente após 120 segundos, desde que você não tenha copiado outra coisa depois
- `LOG_TO_FILE=false`: evita criar `bug_voice_reporter.log` por padrão

## Como Rodar

```powershell
python -m app.main
```

## Como Usar

- `Ctrl+Tab` inicia a gravação
- `Ctrl+Tab` encerra a gravação e inicia o processamento
- `Ctrl+Caps Lock` descarta o áudio atual e reinicia a gravação em 5 segundos
- Não existe mais modal de configuração

## Bandeja Do Sistema

O app cria um ícone na área de notificação com:

- Status atual
- Abrir último bug report quando o histórico local estiver habilitado
- Sair

## Saídas Locais

- `last_output.txt`: última saída formatada, apenas se `SAVE_LAST_OUTPUT=true`
- `last_transcription.txt`: transcrição bruta apenas quando `DEBUG_SAVE_TRANSCRIPTION=true`
- `bug_voice_reporter.log`: log técnico apenas quando `LOG_TO_FILE=true`

## Testes

```powershell
python -m pytest
```

## Troubleshooting

Microfone:

- Confirme o dispositivo de entrada padrão no Windows
- Verifique permissões de microfone no sistema

Gemini:

- Valide a `GEMINI_API_KEY`
- Confira limites de uso da conta
- A aplicação não expõe a chave em notificações e sanitiza mensagens de erro

OpenAI:

- Valide a `OPENAI_API_KEY`
- Confira o projeto e limites de uso da conta
- A aplicação não expõe a chave em notificações e sanitiza mensagens de erro

Hotkeys:

- Alguns ambientes exigem PowerShell com privilégios elevados
- Evite conflito com outros apps usando os mesmos atalhos
