#Requires AutoHotkey v2.0
#SingleInstance Force

targetWindow := "ahk_exe Animo.exe"
botRunning := false

botGui := Gui("+AlwaysOnTop", "Animo Bot")
botGui.SetFont("s10", "Segoe UI")
statusText := botGui.AddText("w260 Center", "พร้อมทำงาน")
startButton := botGui.AddButton("xm w125 h36 Default", "Start (F7)")
stopButton := botGui.AddButton("x+10 w125 h36 Disabled", "Stop (F8)")
botGui.AddText("xm w260 Center c666666", "Tab 250 ms  |  Click 200 ms")

startButton.OnEvent("Click", StartBot)
stopButton.OnEvent("Click", (*) => StopBot())
botGui.OnEvent("Close", (*) => ExitApp())
botGui.Show()

F7::StartBot()
F8::StopBot()

StartBot(*) {
    global targetWindow, botRunning, statusText, startButton, stopButton

    if botRunning
        return

    if !WinExist(targetWindow) {
        MsgBox("ไม่พบ Animo.exe กรุณาเปิดโปรแกรมก่อนแล้วกด Start อีกครั้ง", "Animo Bot", "Icon!")
        return
    }

    WinActivate(targetWindow)
    if !WinWaitActive(targetWindow, , 2) {
        MsgBox("ไม่สามารถสลับไปยังหน้าต่าง Animo.exe ได้", "Animo Bot", "Icon!")
        return
    }

    botRunning := true
    statusText.Text := "กำลังทำงาน... (F8 เพื่อหยุด)"
    startButton.Enabled := false
    stopButton.Enabled := true

    Send("{Tab}")
    SetTimer(DoClick, -250)
}

DoClick() {
    global targetWindow, botRunning

    if !botRunning
        return

    if !WinActive(targetWindow) {
        StopBot("หยุดแล้ว: Animo.exe ไม่ได้เป็นหน้าต่างหลัก")
        return
    }

    Click()
    SetTimer(DoTab, -200)
}

DoTab() {
    global targetWindow, botRunning

    if !botRunning
        return

    if !WinActive(targetWindow) {
        StopBot("หยุดแล้ว: Animo.exe ไม่ได้เป็นหน้าต่างหลัก")
        return
    }

    Send("{Tab}")
    SetTimer(DoClick, -250)
}

StopBot(message := "หยุดแล้ว") {
    global botRunning, statusText, startButton, stopButton

    botRunning := false
    SetTimer(DoClick, 0)
    SetTimer(DoTab, 0)
    statusText.Text := message
    startButton.Enabled := true
    stopButton.Enabled := false
}
