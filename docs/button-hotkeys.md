# Botao USB para o Bugometro

Este fluxo usa o botao USB como uma tecla neutra e deixa o Bugometro continuar
ouvindo o atalho padrao `Ctrl+F1`.

## Recomendacao

Configure o software do botao fisico para enviar `Ctrl+Alt+Shift+F12`. Se o
software aceitar `F24`, essa tambem e uma boa opcao.

`F24` e uma boa tecla sentinela no Windows porque faz parte da familia padrao de
teclas de funcao (`F1` a `F24`), mas quase nenhum teclado fisico ou aplicativo
usa essa tecla no dia a dia. Assim, o botao fica facil de capturar sem brigar
com atalhos reais.

O configurador testado nesta maquina mostra apenas `F1` a `F12`, entao foi usado
`Ctrl+Alt+Shift+F12`: uma combinacao rara o bastante para evitar conflito com
apps comuns.

No botao testado nesta maquina, o Windows detectou o dispositivo como
`VID_8088&PID_0015` e ele continua emitindo `Enter`. Por isso o script tambem
escuta `Enter` como fallback. Nesse modo, toques curtos em `Enter` continuam
funcionando normalmente, mas evite segurar o `Enter` do teclado normal por `3`
segundos, porque isso tambem pode ligar o modo do botao.

Se o software do botao nao aceitar `F24`, use uma destas alternativas:

- `F23`
- `F22`
- `Ctrl+Alt+Shift+F12`

Depois ajuste `buttonKeys` no arquivo [scripts/button_hotkeys.ahk](../scripts/button_hotkeys.ahk).

## Comportamento configurado

1. Pressione o botao uma vez, ou segure por ate `5` segundos.
2. Ao soltar, o script envia `Ctrl+F1` uma unica vez.
3. O Bugometro usa esse `Ctrl+F1` para iniciar ou encerrar a gravacao de voz.

A ponte do botao nao mostra notificacoes. As notificacoes visiveis ficam apenas
com o proprio Bugometro.

## Como usar

1. Abra o Bugometro normalmente pelo atalho `Bugometro`.
2. O launcher `Bugometro.pyw` inicia a ponte AutoHotkey automaticamente.
3. Configure o botao fisico para enviar `Ctrl+Alt+Shift+F12`, ou use o fallback
   atual em que ele envia `Enter`.
4. Teste: pressione o botao uma vez para iniciar e pressione uma vez de novo
   para parar. Tambem pode segurar por ate `5` segundos antes de soltar.

Para diagnostico, a ponte escreve eventos em
`%TEMP%\bugometro_button_hotkeys.log`.

## Ajustes rapidos

No topo de [scripts/button_hotkeys.ahk](../scripts/button_hotkeys.ahk):

```ahk
buttonKeys := [
    "^!+F12",
    "F24",
    "Enter",
]
minimumEnableHoldMs := 350
maximumPressMs := 5000
bugometroVoiceHotkey := "^{F1}"
```

Altere `maximumPressMs` se quiser mudar o limite de tempo do pressionamento.
O fallback `Enter` e interceptado para evitar repeticao automatica enquanto o
botao fica pressionado.
