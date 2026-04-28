# bug-voice-reporter

Aplicativo desktop para Windows que grava relatos de bug por voz, pode capturar a tela localmente, transcreve com Gemini ou OpenAI, monta o bug report e copia o resultado para a area de transferencia.

## O que o app faz

- grava o relato por hotkey global
- captura a tela localmente com video e frames temporarios
- permite anotar com seta usando `Ctrl + arrastar` durante a captura
- transcreve com Gemini ou OpenAI
- organiza a saida em um template de bug report
- copia o texto final para o clipboard

## Fluxos principais

### Voz

1. Pressione `Ctrl+F2` para iniciar a gravacao.
2. Pressione `Ctrl+F2` novamente para encerrar.
3. O app transcreve, formata e copia o bug report final.
4. Use `Ctrl+"` para colar somente o titulo no campo de assunto e depois `Ctrl+V` para colar o restante.

### Voz + captura de tela

1. Pressione `Ctrl+F3` para iniciar a captura de tela local.
2. Reproduza o bug, fale normalmente e use `Ctrl + arrastar` se quiser destacar algo com uma seta.
3. Pressione `Ctrl+F3` novamente para finalizar.
4. O app processa a voz, consolida o video local e copia o bug report final.
5. Use `Ctrl+Shift+V` para copiar o ultimo video e colar no campo de anexo quando precisar.

## Requisitos

- Windows 10 ou 11
- Python 3.11+
- microfone funcional
- `GEMINI_API_KEY` ou `OPENAI_API_KEY`

## Instalacao

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Configuracao

Copie `.env.example` para `.env` e preencha as chaves e opcoes desejadas.

```env
TRANSCRIPTION_PROVIDER=gemini
FORMATTER_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
```

Variaveis mais importantes:

- `APP_HOTKEY`: inicia e encerra a gravacao por voz
- `APP_TITLE_PASTE_HOTKEY`: cola apenas o titulo do ultimo bug report formatado
- `APP_SCREEN_CAPTURE_HOTKEY`: inicia e encerra a captura de tela local
- `APP_VIDEO_ATTACH_HOTKEY`: copia o ultimo video local para anexo
- `DEVTOOLS_MCP_ENABLED`: ativa a leitura opcional de contexto tecnico externo
- `DEVTOOLS_MCP_COMMAND` ou `DEVTOOLS_MCP_CONTEXT_PATH`: apontam para o bridge que fala com Google DevTools MCP
- `AUTO_STOP_ON_SILENCE`: encerra automaticamente apos silencio
- `SAVE_LAST_OUTPUT`, `DEBUG_SAVE_TRANSCRIPTION` e `LOG_TO_FILE`: habilitam persistencia local
- `NATIVE_CAPTURE_TARGET`, `NATIVE_CAPTURE_FPS` e `NATIVE_CAPTURE_FRAME_LIMIT`: ajustam a captura nativa

Os valores padrao e todas as opcoes disponiveis estao em `.env.example`.

## Contexto opcional de DevTools MCP

O app continua funcionando normalmente sem nenhuma integracao adicional. Se voce quiser enriquecer o fluxo de voz + captura local com sinais tecnicos do Google DevTools MCP, habilite:

- `DEVTOOLS_MCP_ENABLED=true`
- `DEVTOOLS_MCP_COMMAND=node scripts/devtools_snapshot.mjs --browser-url http://127.0.0.1:9222 --wait-ms 1500`
- ou `DEVTOOLS_MCP_CONTEXT_PATH` apontando para um arquivo JSON atualizado por um bridge externo

Quando esse contexto estiver disponivel, o app tenta anexar ao prompt sinais como URL, titulo, navegador observado, console, excecoes JavaScript, elemento em foco, resumo de rede, requisicoes lentas ou com falha, recursos pesados, Web Vitals disponiveis, heap JS, long tasks, tempo de script/layout/recalculo de estilo e possiveis gargalos. Se o comando falhar, o arquivo nao existir, o Chrome observado nao estiver aberto ou o JSON vier invalido, o fluxo base segue normalmente sem regressao.

Para usar o bridge local, mantenha um Chrome/Edge iniciado com remote debugging em `http://127.0.0.1:9222`. O script seleciona uma pagina real quando houver varias abas, ignora `about:`/`devtools://` quando possivel e devolve apenas sinais tecnicos para enriquecer o bug report.

Observacao: o MCP conectado ao chat do Codex e o processo local do app sao ambientes separados. Para o texto copiado pelo app receber esses dados, o app precisa acessar um bridge local via `DEVTOOLS_MCP_COMMAND` ou um JSON em `DEVTOOLS_MCP_CONTEXT_PATH`.

## Como executar

```powershell
python -m app.main
```

## Testes

```powershell
python -m pytest
```

## Privacidade

- o app nao salva historico permanente por padrao
- a transcricao bruta e a saida final so sao gravadas se isso for habilitado no `.env`
- logs em arquivo ficam desativados por padrao
- evidencias locais ficam em pasta temporaria e podem ser limpas automaticamente
- o conteudo copiado pode ser limpo automaticamente do clipboard com `CLIPBOARD_CLEAR_SECONDS`

## Saidas opcionais

- `last_output.txt`
- `last_transcription.txt`
- `bug_voice_reporter.log`
- pacote temporario `bug_voice_reporter_capture_*`

## Troubleshooting

- Verifique se o microfone esta disponivel no Windows.
- Confirme que as hotkeys nao entram em conflito com outros apps.
- Valide a chave e o provedor configurado no `.env`.
- Se a captura de tela falhar, revise as dependencias instaladas em `requirements.txt`.

## Arquitetura

Detalhes sobre a estrategia de captura nativa do Windows estao em [docs/native-windows-architecture.md](docs/native-windows-architecture.md).
