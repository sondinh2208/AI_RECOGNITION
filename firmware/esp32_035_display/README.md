# FaceCheck ESP32-035 display

Firmware cho bo man hinh ESP32-035, TFT 3.5 inch 320 x 480.

Man hinh ket noi Wi-Fi 2.4 GHz, nhan anh JPEG 480x250 va trang thai kiosk tu
laptop. Camera OV3660 van truyen truc tiep bang USB-OTG den laptop, vi vay bo
man hinh khong mo them camera va khong can xu ly AI.

## Chan man hinh

- MOSI: GPIO 13
- MISO: GPIO 12
- SCLK: GPIO 14
- TFT CS: GPIO 15
- TFT DC: GPIO 2
- Backlight: GPIO 27
- Touch CS: GPIO 33
- Touch IRQ: GPIO 36

TFT reset duoc noi chung voi chan EN cua bo mach.

## Kiem tra phan cung

Mac dinh thu driver ST7796:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe" run -d firmware\esp32_035_display -e esp32-035-st7796 -t upload
```

Neu man hinh trang, den hoac nhieu, thu driver ILI9488:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe" run -d firmware\esp32_035_display -e esp32-035-ili9488 -t upload
```

Ban sao firmware cua nha san xuat duoc luu tai `factory_backup.bin` va bi Git bo qua.

## Cau hinh ket noi

Sao chep `secrets.example.h` thanh `include/secrets.h`, sau do dien Wi-Fi 2.4 GHz va
IPv4 cua laptop. `secrets.h` bi Git bo qua de khong dua mat khau len repository.

Laptop phai cho phep ket noi TCP noi bo den cong 8767. Kenh nay chi co API doc
danh cho TFT va bat buoc co khoa `X-FaceCheck-Token`. Toan bo giao dien quan tri
van chi lang nghe tai `http://127.0.0.1:8765`, khong duoc cong khai ra LAN.
