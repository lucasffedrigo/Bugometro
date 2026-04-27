# bug-voice-reporter

App desktop leve para Windows que captura relatos de bug por voz, transcreve com Gemini ou OpenAI, formata em bug report estruturado e copia o resultado para a área de transferência.

O app opera com privacidade por padrão: não salva relatório local, não salva transcrição bruta e não grava log em arquivo, a menos que você habilite isso no `.env`.

## Visão Geral

Fluxo principal:

1. Pressione `Ctrl+F2`
2. Fale livremente o relato
3. Pressione `Ctrl+F2` novamente para encerrar manualmente
4. O modal inicial some sozinho após 10 segundos, mas a gravação continua
5. O áudio é enviado ao provedor configurado para transcrição
6. A transcrição vira um bug report estruturado
7. O texto final é copiado para o clipboard

Fluxo robusto com BugReel:

1. Pressione `Ctrl+F3`
2. O app arma o monitoramento, foca o Chrome e dispara a hotkey interna da extensão
3. Se necessário, finalize a captura na extensão normalmente
4. O app aguarda a próxima gravação encerrada no BugReel
5. Quando ela aparecer, o app consulta a API do BugReel
6. O app tenta importar:
   - título e resumo gerados pelo BugReel
   - transcrição do vídeo
   - metadados de ambiente
   - eventos de console
   - ações do usuário
   - navegações/URLs
   - keyframes/prints
   - URL do vídeo
6. O resultado final sai no template com contexto enriquecido e evidências

O contexto BugReel é de uso único: após um relatório bem-sucedido, ele é limpo da memória.

## Arquitetura de Uso

- `Ctrl+F2`: inicia e finaliza a gravação por voz
- `Ctrl+Caps Lock`: opcional, descarta o áudio atual e reinicia uma nova gravação em 5 segundos
- `Ctrl+F3`: arma/inicia ou encerra a captura do BugReel
- `Ctrl+Shift+V`: copia o último vídeo BugReel para colar no campo de anexo

Com isso, você tem dois modos:

- voz pura
- voz + BugReel

## Segurança

- Nada é salvo localmente por padrão como histórico permanente
- Quando há BugReel vinculado, o app pode baixar vídeo e prints para uma pasta temporária de evidências
- O pacote temporário é usado para complementar o contexto e facilitar anexos
- O relatório final copia o template em texto no clipboard
- Opcionalmente, o app também pode publicar arquivos locais de evidência no clipboard (`CLIPBOARD_INCLUDE_FILES=true`)
- Para endurecer a importação de contexto, configure `BUGREEL_BASE_URL` no `.env`

Importante:

- colar texto + arquivo no mesmo `Ctrl+V` depende do aplicativo de destino
- quando `CLIPBOARD_INCLUDE_FILES=true`, alguns apps vão preferir o texto e outros os arquivos
- por isso, o template final também inclui o caminho do pacote local de evidências quando ele existir

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
- Contexto opcional e enriquecido de BugReel

## Requisitos

- Windows 10 ou Windows 11
- Python 3.11+
- Microfone funcional
- Chave do Gemini ou da OpenAI
- Instância privada do BugReel, se quiser usar contexto com vídeo/evidência

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

- `APP_HOTKEY=ctrl+f2`
- `APP_RESTART_HOTKEY=` (vazio para desativar)
- `APP_BUGREEL_HOTKEY=ctrl+f3`
- `APP_VIDEO_ATTACH_HOTKEY=ctrl+shift+v`
- `BUGREEL_AUTO_TRIGGER=true`
- `BUGREEL_COMMAND_HOTKEY=alt+shift+r`
- `BUGREEL_AUTO_FOCUS_CHROME=true`
- `BUGREEL_BOOT_URL=` (vazio para não abrir nova aba automaticamente)
- `BUGREEL_TRIGGER_DELAY_SECONDS=1.2`
- `BUGREEL_AUTO_START_CONTAINER=true`
- `BUGREEL_COMPOSE_DIR=C:\Users\lucas\Desktop\bugreel`
- `BUGREEL_START_TIMEOUT_SECONDS=25`

Configuração recomendada para BugReel privado:

```env
BUGREEL_BASE_URL=https://seu-host-privado-do-bugreel
BUGREEL_API_TOKEN=seu_extension_token_ou_token_privado
BUGREEL_TIMEOUT_SECONDS=20
BUGREEL_CAPTURE_TIMEOUT_SECONDS=90
BUGREEL_DOWNLOAD_EVIDENCE=true
BUGREEL_FRAME_LIMIT=3
```

Se `BUGREEL_API_TOKEN` estiver preenchida, o app consegue consultar rotas privadas do BugReel com mais confiabilidade.

Flags úteis:

- `SAVE_LAST_OUTPUT=false`: não grava `last_output.txt` por padrão
- `DEBUG_SAVE_TRANSCRIPTION=false`: não grava `last_transcription.txt` por padrão
- `AUTO_STOP_ON_SILENCE=false`: mantém a gravação ativa mesmo após silêncio; o encerramento padrão é manual pela hotkey
- `CLIPBOARD_CLEAR_SECONDS=120`: limpa o conteúdo copiado automaticamente após 120 segundos, desde que você não tenha copiado outra coisa depois
- `CLIPBOARD_INCLUDE_FILES=false`: mantém o `Ctrl+V` priorizando o template em texto
- `LOG_TO_FILE=false`: evita criar `bug_voice_reporter.log` por padrão

## Como Rodar

```powershell
python -m app.main
```

## Como Usar

- `Ctrl+F2` inicia a gravação
- `Ctrl+F2` encerra a gravação e inicia o processamento
- se `APP_RESTART_HOTKEY` estiver configurada, essa hotkey descarta o áudio atual e reinicia a gravação em 5 segundos
- pressione `Ctrl+F3` para iniciar o fluxo BugReel (preflight + abertura do painel de compartilhamento)
- pressione `Ctrl+F3` novamente para encerrar a captura BugReel e iniciar o processamento
- após colar o template com `Ctrl+V`, pressione `Ctrl+Shift+V` para copiar o último vídeo e cole no campo de anexo
- O HUD de instrução fecha em até 10 segundos sem encerrar a gravação
- Se houver BugReel vinculado, o app tenta baixar vídeo e prints para um pacote temporário
- O template final inclui as evidências e, se `CLIPBOARD_INCLUDE_FILES=true`, o clipboard do Windows também recebe os arquivos locais

## Bandeja do Sistema

O app cria um ícone na área de notificação com:

- Status atual
- Abrir último bug report quando o histórico local estiver habilitado
- Sair

## Saídas Locais

- `last_output.txt`: última saída formatada, apenas se `SAVE_LAST_OUTPUT=true`
- `last_transcription.txt`: transcrição bruta apenas quando `DEBUG_SAVE_TRANSCRIPTION=true`
- `bug_voice_reporter.log`: log técnico apenas quando `LOG_TO_FILE=true`
- `bug_voice_reporter_bugreel_*`: pacote temporário de evidências do BugReel, criado apenas quando houver integração ativa

## Testes

```powershell
python -m pytest
```

## Troubleshooting

Microfone:

- Confirme o dispositivo de entrada padrão no Windows
- Verifique permissões de microfone no sistema

BugReel:

- Confirme que o BugReel está no ar em `BUGREEL_BASE_URL`
- Confirme no Chrome que o comando `Toggle BugReel recording` está em `Alt+Shift+R`
- Configure `BUGREEL_BASE_URL` para evitar importar links de outro host por engano
- Configure `BUGREEL_API_TOKEN` se a instância exigir autenticação para a API
- Se o app não conseguir baixar o vídeo ou os prints, ele ainda continua com o relatório e os metadados como evidência

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
