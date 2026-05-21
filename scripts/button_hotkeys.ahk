#Requires AutoHotkey v2.0
#SingleInstance Force
#UseHook

Persistent

; Preferred setup: configure the physical USB button to emit F24.
; Configured setup: the vendor app maps the button to Ctrl+Alt+Shift+F12.
; Fallback: before configuration, the connected button was detected emitting Enter.
buttonKeys := [
    "^!+F12",
    "F24"
]
maximumPressMs := 5000
bugometroVoiceHotkey := "^{F1}"
logPath := A_Temp "\bugometro_button_hotkeys.log"

pressedKeys := Map()

for buttonKey in buttonKeys {
    Hotkey "*" . buttonKey, HandleButtonDown, "On"
}
Log("Button bridge started.")

HandleButtonDown(thisHotkey) {
    global maximumPressMs
    global bugometroVoiceHotkey
    global pressedKeys

    buttonKey := RegExReplace(thisHotkey, "^[~*$<>!^+#]+")
    Log("Button down: " buttonKey)
    if (pressedKeys.Has(buttonKey)) {
        Log("Ignored repeat: " buttonKey)
        return
    }
    pressedKeys[buttonKey] := true

    KeyWait buttonKey, "T" . (maximumPressMs / 1000)
    KeyWait buttonKey
    pressedKeys.Delete(buttonKey)
    Send bugometroVoiceHotkey
    Log("Sent voice hotkey from " buttonKey ".")
}

Log(message) {
    global logPath

    FileAppend FormatTime(, "yyyy-MM-dd HH:mm:ss") " " message "`n", logPath
}
