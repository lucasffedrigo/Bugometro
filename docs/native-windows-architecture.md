# Arquitetura nativa Windows

## Direção recomendada

- Camada de orquestração: manter o app principal em Python para hotkeys, UX, clipboard, privacidade e chamadas de IA.
- Camada de captura: isolar a gravação em um backend nativo de Windows com interface estável para que possamos trocar o motor sem alterar o fluxo do produto.
- Camada de evidência: produzir sempre um pacote temporário local com vídeo e alguns frames de apoio, sem upload automático.
- Camada de anotação: desenhar via overlay transparente e também compor o desenho no frame antes da codificação, para garantir que a anotação apareça ao vivo e no vídeo final.

## Backend ideal

- Captura de tela/janela: `Windows.Graphics.Capture` ou `Desktop Duplication API`, preferencialmente via helper dedicado em C# ou Rust para maior previsibilidade.
- Encoding: `Media Foundation` com H.264 ou um codec otimizado para screen capture.
- Seleção de alvo: janela ativa por padrão e, em uma segunda fase, picker nativo de janela/tela.

## Primeira versão funcional neste repositório

- Captura: `mss` para capturar a janela ativa ou o desktop.
- Vídeo: `imageio` + `imageio-ffmpeg` para gerar `mp4`.
- Voz: reaproveitamento do gravador atual com `sounddevice`.
- Anotação: `pynput` para observar `Shift + arrastar`, overlay click-through em `tkinter` + Win32, e composição do desenho no frame com `Pillow`.

## Trade-offs

- V1 prioriza fluxo local, simples e sem Chrome/Docker, mas ainda não usa o stack final de `Windows.Graphics.Capture`.
- V1 captura a janela ativa pela área visível; não grava uma janela minimizada ou totalmente oculta.
- O vídeo da evidência é salvo temporariamente sem áudio embutido; a voz continua sendo a fonte principal para a IA.
