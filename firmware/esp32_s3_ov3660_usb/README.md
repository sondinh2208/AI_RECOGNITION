# FaceCheck camera qua USB-OTG

Firmware nay gui JPEG VGA tu OV3660 sang laptop bang cong USB-OTG, khong dung
Wi-Fi. Cong USB-UART hoac USB-OTG deu co the nap firmware; sau khi nap, cam day
vao cong USB-OTG de Windows tao mot cong COM native USB.

Quy trinh:

1. Nap firmware trong thu muc nay.
2. Cam cong USB-OTG vao laptop va doi 3-5 giay.
3. Tai thu muc goc du an, chay `python start_usb_facecheck.py`.
4. Cau noi se tu tim cong USB cua Espressif va cung cap stream tai
   `http://127.0.0.1:8766/stream`.

Endpoint kiem tra:

- `http://127.0.0.1:8766/`
- `http://127.0.0.1:8766/capture`
- `http://127.0.0.1:8766/health`

