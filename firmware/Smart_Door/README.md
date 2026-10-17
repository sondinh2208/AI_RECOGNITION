# Smart Door Controller

## Board va PlatformIO

Project duoc cau hinh cho ESP32-S3-WROOM-1-N16R8:

- 16 MB QSPI Flash
- 8 MB OPI PSRAM
- PlatformIO environment: `esp32-s3-n16r8`

Build va nap firmware:

```powershell
pio run -d firmware/Smart_Door
pio run -d firmware/Smart_Door -t upload
pio device monitor -d firmware/Smart_Door
```

Neu ma tren vo module la `N16R8V` thay vi `N16R8`, khong nap cau hinh nay:
ban `V` dung OPI Flash va can doi memory type.

## Thu vien

- Adafruit Fingerprint Sensor Library
- Adafruit SSD1306
- Adafruit GFX Library
- UniversalTelegramBot (tam giu de firmware cu van build duoc)
- ArduinoJson (dependency cua UniversalTelegramBot)
- PubSubClient by Nick O'Leary

PlatformIO tu cai cac thu vien tren theo `platformio.ini`.

Chon dung board ESP32-S3 va cai ESP32 Arduino Core phu hop voi board dang su dung.

## Cau hinh rieng tu

Sao chep `secrets.example.h` thanh `secrets.h` va dien Wi-Fi/MQTT. Tep
`secrets.h` bi Git bo qua. Khong dua mat khau vao `config.h`.

## MQTT heartbeat, PING va OPEN

Firmware ket noi HiveMQ Cloud bang MQTT/TLS cong 8883 va CA ISRG Root X1.
NTP phai dong bo thanh cong truoc khi bat dau TLS. Moi 30 giay, ESP32 publish
retained status toi:

```text
smartdoor/v1/door-01/status
```

Firmware nhan `PING` va `OPEN` tai:

```text
smartdoor/v1/door-01/command
```

va tra ket qua tai `smartdoor/v1/door-01/ack`. Lenh `PING` khong the kich relay.

Lenh `OPEN` hop le co dang:

```json
{
  "command": "OPEN",
  "command_id": "open-0123456789ab",
  "device_id": "door-01",
  "duration_ms": 3000,
  "issued_at": 1792224000,
  "expires_at": 1792224010
}
```

Quy tac an toan:

- MQTT callback chi xac thuc va dua lenh vao FreeRTOS queue; `Task_LockControl`
  la noi duy nhat dieu khien relay.
- `command_id` chi duoc chua chu, so, `-`, `_`, `.` va toi da 48 ky tu.
- `duration_ms` nam trong 500-10000 ms.
- `expires_at` la Unix epoch bat buoc, chua het han va khong xa hon 15 giay.
- Cac `command_id` gan day duoc ghi nho trong RAM de chan lenh lap.
- ACK cua lenh hop le di qua `started` roi `finished`; lenh bi tu choi tra
  `failed` kem `reason`.
- Command phai publish voi `retain=false`. TTL van bao ve neu server gui nham
  retained message.

Van tay va nut thoat van mo cua cuc bo khi Wi-Fi/MQTT mat ket noi. MQTT khong
nam tren duong quyet dinh cua chuc nang offline.

## Kiem tra heartbeat tu may server

```powershell
pip install -r requirements.txt
python tools/mqtt_door_monitor.py
python tools/mqtt_door_ping.py
```

Chi test mo cua khi khu vuc co khi an toan:

```powershell
python tools/mqtt_door_open.py --duration-ms 3000 --confirm-open
```

Neu thieu `--confirm-open`, cong cu se dung ma khong publish lenh.

Ket qua mong doi sau khi nap firmware:

```text
[SYSTEM] Flash: 16 MB | PSRAM: 8 MB
[MQTT] Da ket noi TLS ...
[smartdoor/v1/door-01/status]
{"device_id":"door-01","state":"online", ...}
```
