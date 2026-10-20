# Aniimo Bot

บอต Windows สำหรับ `aniimo.exe`: กด Tab/คลิกวนซ้ำ ตรวจ UI ด้วย OpenCV
และหยุดเมื่อ OCR พบคำที่ตั้งไว้ เช่น “คลังเต็ม” พร้อมแจ้ง Discord หากตั้ง Webhook

## โหมดควบคุม

- **Background** (ค่าเริ่มต้น): จับภาพพื้นที่เกมผ่าน
  `PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT)` และส่ง Tab/คลิกผ่าน
  `PostMessage` ไปยัง HWND ของเกมหรือหน้าต่างย่อยที่ตรงกับตำแหน่งคลิก
  รองรับ Tab, Middle Mouse, Mouse 4 และ Mouse 5 เป็นคำสั่งล็อกเป้า
  คลิกซ้าย/ขวาส่งตรงเข้า HWND โดยไม่ขยับเมาส์ ส่วนปุ่มล็อกของ Aniimo ไม่รับ
  `PostMessage` รุ่น v2.5 จึงเปิดโฟกัสเกมประมาณ 180 ms ส่ง Tab จริง แล้วคืนหน้าต่างเดิม
  ทันที ระหว่างนั้นรักษาหน้าต่างเดิมไว้เหนือเกม โดยไม่ย้ายเมาส์ ไม่ใช้ `BlockInput`
  และไม่ค้างเกมไว้ด้านหน้า
- **Foreground**: จับภาพบนจอและใช้คีย์บอร์ด/เมาส์แบบเดิม
  เกมต้องเป็นหน้าต่างหลัก และบอตหยุดเมื่อสลับไปโปรแกรมอื่น
- **Hybrid**: จับภาพ/ยิงแบบ Background แต่ดึงเกมขึ้นหน้าและย้ายเมาส์เข้าเกม
  ชั่วคราวเพื่อส่งปุ่มล็อกจริง แล้วคืนโปรแกรมและตำแหน่งเมาส์เดิม
  หากผู้ใช้สลับหน้าต่างหรือขยับเมาส์ระหว่างนั้น จะรักษาการเปลี่ยนของผู้ใช้
  หาก Windows ไม่ยอมเปลี่ยน foreground จะหยุดโดยไม่ส่งปุ่มล็อก

รุ่น **v2.5** ใช้ลูปสถานะแบบตายตัว: กดปุ่มล็อกหนึ่งครั้ง → รอ Delay หลังล็อก
→ คลิกซ้ายหนึ่งครั้ง → รอ Delay หลังคลิก → เริ่มรอบใหม่ด้วยการกดปุ่มล็อกเสมอ
ไม่รอตรวจกรอบล็อกสีขาว จึงทำงานตามเวลาที่ตั้งไว้โดยตรง และไม่คลิกซ้ำจาก
กรอบล็อกของเป้าเดิมที่ยังค้างหลังโยนบอล
ผลทดสอบเดิมกับ Aniimo ยืนยันว่าคลิกซ้ายผ่าน HWND ทำงาน แต่ Tab ผ่านข้อความไม่ทำงาน
Player.log ระบุว่าเกมใช้ Unity input และ `Windows.Gaming.Input` จึงใช้ focus assist
เฉพาะช่วงส่งปุ่มล็อกแทน การทำงานเมื่อย่อเกมยังไม่ได้ยืนยัน
เริ่มทดสอบโดยเปิดเกมแบบ Windowed/Borderless แล้วสลับไปโปรแกรมอื่น
ก่อนทดลองย่อหน้าต่างจริง

## ใช้งาน

1. เปิด `aniimo.exe`
2. ปิดบอตรุ่นเดิม แล้วเปิด `AnimoBot_Background_Fix.exe` (v2.5) หรือ `run_animo_bot.bat`
3. เลือก **Background** แล้วกด **ตรวจภาพเกม (ไม่ส่งอินพุต)**
   ตรวจว่าภาพเป็นเกมจริงและตำแหน่ง UI ถูกต้อง
   ภาพล่าสุดบันทึกไว้ใน `background_capture_preview.png`
4. เลือกปุ่มล็อกให้ตรงกับเกม เช่น **Middle Mouse** คือกดลูกกลิ้งลง
   เลือก **Foreground** เมื่อต้องการให้เกมอยู่ด้านหน้า หรือ **Hybrid** หากยอมให้เกมขึ้นหน้า
   ชั่วคราวตอนล็อก ส่วน **Background** จะสลับโฟกัสประมาณ 180 ms แต่รักษาโปรแกรมเดิม
   ไว้เหนือเกมและคืนโฟกัสทันที
5. ตั้งคำที่ต้องตรวจ, Delay และ Webhook (เว้นว่างได้) แล้วกด **START BOT**
   บอตจะกดปุ่มล็อก รอตาม Delay แล้วคลิกซ้ายหนึ่งครั้งโดยไม่ตรวจกรอบล็อกสีขาว
   กด **STOP** เพื่อยกเลิกได้ทั้งช่วงเตรียม OCR และช่วงทำงาน

ค่าเริ่มต้นใหม่: Background, ปุ่มล็อก Tab, หลังล็อก 200 ms, หลังคลิก 250 ms,
รอบ OCR ประมาณ 1 วินาที
UI ใช้ภาพอ้างอิง `anipod_ui_anchor.png` คะแนนขั้นต่ำ 80%
และปรับสเกลตามความกว้างของพื้นที่เกม

## เงื่อนไขหยุด

- พบคำที่ตั้งไว้: หยุดส่งอินพุตก่อนรอ Discord
- เกมถูกปิด, จับภาพล้มเหลว/หมดเวลา, ภาพว่างหรือ OCR ผิดพลาด
- ภาพหลังย่อหน้าต่างไม่เปลี่ยนเกิน 5 วินาที
  ซึ่งอาจเกิดได้กับฉากนิ่งด้วย ให้เปิดหน้าต่างเกมไว้หากพบกรณีนี้
- เปิด UI ด้วยคลิกขวาไม่สำเร็จหลังลอง 3 ครั้ง

หาก UI หาย จะพัก Tab/คลิกซ้ายระหว่างตรวจและเปิด UI กลับ
ภาพเก่าที่ไม่ได้อัปเดตเกิน 1.5 วินาทีจะไม่ถูกนำไปอนุญาตอินพุต
การจับภาพใช้โปรเซสแยก เพื่อหยุดงานได้หาก `PrintWindow` ค้าง
ไม่มีการสลับกลับไปใช้เมาส์จริงอัตโนมัติเมื่อ Background ล้มเหลว

## รันจาก Python

ใช้ Windows และ Python 3.12 ที่มี Tkinter:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe animo_bot.py
```

ติดตั้ง Tesseract OCR พร้อม `tha.traineddata` และ `eng.traineddata`
แล้วเลือก `tesseract.exe` ในหน้าต่างโปรแกรม
ค่าเริ่มต้นค้นที่ `C:\Program Files\Tesseract-OCR`
โปรแกรม `.exe` ที่ build ตาม spec นี้รวม Tesseract และไฟล์ภาษาไว้แล้ว

## Build และตรวจสอบ

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\build_animo_bot.bat
.\.venv\Scripts\python.exe tests\verify_frozen.py
```

ผลลัพธ์คือ `AnimoBot_Background_Fix.exe` และ `dist/AnimoBot_Background_Fix.exe`
ใช้ `AnimoBot_Background.spec` เป็น configuration สำหรับ PyInstaller
หาก Tesseract อยู่ตำแหน่งอื่น ตั้ง `ANIIMO_TESSERACT_DIR` ก่อน build

ชุดทดสอบสร้างหน้าต่าง GDI ของตัวเอง เพื่อตรวจภาพข้ามโปรเซส,
พิกัดของ HWND ย่อย, active/inactive ก่อน–หลังส่งอินพุต, key-up หลัง Stop,
timeout และการป้องกันผลลัพธ์จากรอบเก่า
ผลทดสอบเหล่านี้ยืนยัน backend และวงจรหยุด ไม่ใช่ความเข้ากันได้ของเกม Aniimo
ต้องรันใน Windows desktop session ปกติ; sandbox บางชนิดอาจคืนภาพว่าง
`verify_frozen.py` ตรวจโปรเซสจับภาพของไฟล์ .exe, OCR ที่รวมไว้
และการเปิด/ปิด GUI โดยไม่ส่งคำสั่งเข้าเกม
ชุดทดสอบล็อกเป้าใช้ภาพอ้างอิงจากผู้ใช้และภาพกรอบเล็งที่ยังไม่ล็อก
เพื่อตรวจว่าไม่อนุญาตยิงเพียงเพราะมีกรอบเล็งหรือหน้าตัวละครสีขาว

## ไฟล์หลัก

- `animo_bot.py`: GUI, session, automation, UI detection, OCR และ Discord
- `capture_cycle.py`: state machine บังคับลำดับ Tab → lock → คลิกหนึ่งครั้ง
- `windows_backend.py`: จับภาพ HWND, cache เฟรม และ Background input
- `target_lock.py` / `target_lock_anchor.png`: เครื่องมือตรวจกรอบล็อกสำหรับการวินิจฉัยและชุดทดสอบ
- `animo_bot_settings.json`: ค่าตั้งต้น (ไม่รวมใน Git)
- `log.txt`: log แบบหมุนไฟล์ พร้อม `ui_captures/` เก็บภาพ debug ล่าสุด 40 ภาพ
- `animo_bot.ahk`: สคริปต์ AutoHotkey แบบพื้นฐาน แยกจากเวอร์ชัน Python

Webhook ปกติใช้ Windows DPAPI ผูกกับบัญชี Windows;
หาก DPAPI ใช้ไม่ได้ โค้ดมี fallback บันทึก Webhook เป็นข้อความธรรมดา
จึงไม่ควรส่งไฟล์ settings ให้ผู้อื่น
