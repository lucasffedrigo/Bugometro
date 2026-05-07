# Bugômetro

Aplicativo desktop para Windows que grava relatos de bug por voz, pode capturar a tela localmente, transcreve com Gemini ou OpenAI, monta o bug report e copia o corpo do resultado para a area de transferencia.

## O que o app faz

- grava o relato por hotkey global
- captura a tela localmente com GIF e frames temporarios
- permite anotar com seta pressionando o scroll do mouse e arrastando durante a captura
- transcreve com Gemini ou OpenAI
- organiza a saida em um template de bug report
- copia o corpo do bug report para o clipboard

## Fluxos principais

### Voz

1. Pressione `Ctrl+Shift` para iniciar a gravacao.
2. Se quiser adicionar evidencia visual enquanto continua falando, pressione `Ctrl+Shift+Alt` para iniciar o GIF incremental.
3. Pressione `Ctrl+Shift+Alt` novamente para encerrar apenas o GIF e continuar narrando.
4. Pressione `Ctrl+Shift` novamente para encerrar o audio. Se o GIF ainda estiver rodando, o app encerra GIF e audio juntos, mas prioriza liberar o bug report em texto primeiro.
5. O app transcreve, formata e copia o corpo do bug report; o GIF continua finalizando em segundo plano quando necessario.
6. Use `Ctrl+"` para colar somente o titulo no campo de assunto e depois `Ctrl+V` para colar o restante.

### Voz + captura de tela

1. Pressione `Ctrl+Shift+Espaco` para iniciar a captura de tela local.
2. Reproduza o bug, fale normalmente e pressione o scroll do mouse enquanto arrasta se quiser destacar algo com uma seta.
3. Pressione `Ctrl+Shift+Espaco` novamente para finalizar.
4. O app processa a voz, consolida o GIF local e copia o corpo do bug report.
5. Foque o campo de anexo e use `Ctrl+Shift+V` para colar o ultimo GIF quando precisar.

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
- `APP_VOICE_GIF_HOTKEY`: inicia e encerra um GIF incremental enquanto a voz continua gravando
- `APP_VIDEO_ATTACH_HOTKEY`: cola o ultimo GIF local no campo de anexo em foco
- `DEVTOOLS_MCP_ENABLED`: ativa a leitura opcional de contexto tecnico externo
- `DEVTOOLS_MCP_COMMAND` ou `DEVTOOLS_MCP_CONTEXT_PATH`: apontam para o bridge que fala com Google DevTools MCP
- `AUTO_STOP_ON_SILENCE`: encerra automaticamente apos silencio
- `SAVE_LAST_OUTPUT`, `DEBUG_SAVE_TRANSCRIPTION` e `LOG_TO_FILE`: habilitam persistencia local
- `NATIVE_CAPTURE_TARGET`, `NATIVE_CAPTURE_FPS` e `NATIVE_CAPTURE_FRAME_LIMIT`: ajustam a captura nativa; por padrao `NATIVE_CAPTURE_TARGET=desktop` captura a tela inteira

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

Abra pelo atalho **Bugômetro** na area de trabalho para iniciar sem terminal.

Para recriar o atalho quando necessario:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1
```

Tambem e possivel abrir diretamente o launcher `Bugômetro.pyw`.

Modo terminal para desenvolvimento:

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
- `bugometro.log`
- pacote temporario `bugometro_capture_*`

## Troubleshooting

- Verifique se o microfone esta disponivel no Windows.
- Confirme que as hotkeys nao entram em conflito com outros apps.
- Valide a chave e o provedor configurado no `.env`.
- Se a captura de tela falhar, revise as dependencias instaladas em `requirements.txt`.

## Arquitetura

Detalhes sobre a estrategia de captura nativa do Windows estao em [docs/native-windows-architecture.md](docs/native-windows-architecture.md).
