# FaceCheck — Hệ thống quản lý ra vào tích hợp nhận diện khuôn mặt và cảm biến vân tay

FaceCheck là hệ thống quản lý ra vào tích hợp nhận diện khuôn mặt và cảm biến vân tay. Server nhận hình ảnh từ camera ESP32-S3 OV3660, xử lý YOLO/ArcFace, quản lý hồ sơ và nhật ký xác thực; ESP32-S3 cửa xử lý AS608, OLED, nút và relay, đồng thời vẫn cho phép vân tay hoạt động khi mất mạng.

Ứng dụng có giao diện quản trị dạng web chạy trong cửa sổ desktop, chế độ kiosk dành cho người điểm danh, API nội bộ, luồng camera MJPEG và firmware cho camera/màn hình ESP32.

> **Phạm vi hiện tại:** phần mềm quyết định nhận diện, ghi lịch sử và gửi lệnh `OPEN` ngắn hạn qua MQTT/TLS cho ESP32-S3 điều khiển khóa. Vân tay và nút thoát vẫn hoạt động offline trực tiếp trên ESP32.

## 1. Chức năng chính

- Nhận camera từ ba nguồn: webcam USB/laptop, ESP32 qua USB-OTG hoặc ESP32 qua Wi-Fi.
- Phát hiện khuôn mặt bằng mô hình YOLOv8 tùy biến trong `models/model.pt`.
- Kiểm tra landmark, tư thế, độ sáng và chất lượng mặt bằng MediaPipe.
- Nhận diện danh tính bằng embedding ArcFace 512 chiều và cosine distance.
- Gộp kết quả từ nhiều frame, kiểm tra khoảng cách Top 1/Top 2 và xác nhận theo thời gian để giảm nhận nhầm do ảnh JPEG nhiễu.
- Đăng ký nhiều nhân viên liên tiếp, tự động chụp khi khuôn mặt đạt yêu cầu.
- Quản lý hồ sơ: tìm kiếm, sửa, bật/tắt nhận diện và xóa nhân viên.
- Theo dõi dashboard và lịch sử xác thực thành công/thất bại.
- Kiểm thử một ảnh tĩnh mà không ghi vào lịch sử điểm danh.
- Đồng bộ trạng thái và ảnh đến màn hình TFT ESP32-035 qua một kênh chỉ đọc có token.
- Tự kết nối lại camera khi nguồn hình bị gián đoạn.

## 2. Công nghệ sử dụng

- **Python 3.11**: runtime chính của dự án.
- **OpenCV**: đọc camera, xử lý frame, encode/decode JPEG và vẽ HUD.
- **Ultralytics YOLOv8**: phát hiện khuôn mặt.
- **MediaPipe Face Detector**: landmark và kiểm tra tư thế/chất lượng.
- **DeepFace + ArcFace**: tạo embedding và nhận diện danh tính.
- **Starlette + Uvicorn**: HTTP API, WebSocket trạng thái và MJPEG stream.
- **PyWebView**: đóng gói Web UI thành cửa sổ desktop.
- **HTML/CSS/JavaScript thuần**: giao diện quản trị và kiosk.
- **PlatformIO/Arduino**: firmware cho ESP32-S3 camera và ESP32-035 TFT.

## 3. Kiến trúc tổng thể

```text
Webcam / ESP32 USB / ESP32 Wi-Fi
              │
              ▼
        Camera Capture
              │ frame mới nhất
              ▼
     YOLO phát hiện khuôn mặt
              │
              ├──────────────► MediaPipe kiểm tra chất lượng/tư thế
              │                              │
              ▼                              ▼
       Cắt + căn chỉnh mặt ◄────────── khuôn mặt hợp lệ
              │
              ▼
       ArcFace embedding 512-D
              │
              ▼
  Gộp nhiều frame + so khớp cosine
              │
       ┌──────┼─────────┐
       ▼      ▼         ▼
  Thành công  Mơ hồ   Người lạ
       │      │         │
       └──────┴─────────┘
              │
              ▼
  Runtime state + lịch sử JSON
              │
       ┌──────┼────────────┐
       ▼      ▼            ▼
   Admin UI  Kiosk UI   TFT ESP32-035
```

Các lớp được tách theo trách nhiệm:

- `facecheck.py` là launcher duy nhất, nạp `.env`, chọn nguồn camera và quản lý camera bridge nếu cần.
- `web_admin.py` giữ cổng backend, khởi động Uvicorn, mở cửa sổ PyWebView và dừng dịch vụ khi cửa sổ đóng.
- `web_backend/app.py` chỉ vận chuyển dữ liệu qua REST, WebSocket và MJPEG; quyết định nghiệp vụ nằm trong runtime.
- `web_runtime.py` giữ state dùng chung, điều phối chế độ, CRUD nhân viên, lịch sử và dữ liệu gửi đến UI/TFT.
- `door_service.py` quản lý MQTT/TLS, ACK mở cửa, timeout, cooldown và điều kiện khuôn mặt phải rời camera trước lượt mới.
- `mixins/attendance.py` chứa pipeline nhận diện và chính sách quyết định điểm danh.
- `mixins/camera.py` chứa pipeline camera/kiểm tra chất lượng khi đăng ký.
- `mixins/enrollment.py` tạo embedding và lưu hồ sơ mới.
- `mixins/core.py` tải model, cache embedding, warm-up ArcFace và đọc/ghi lịch sử.
- `ai_engine.py` chứa các hàm AI mức thấp: detect, quality check, alignment và cosine distance.
- `camera_capture.py` đọc MJPEG theo chiến lược “latest frame”, tránh xử lý backlog cũ.
- `ekyc_renderer.py` dựng vùng hướng dẫn và HUD cho quá trình đăng ký.
- `web_ui/` chứa toàn bộ giao diện frontend.

Camera và model chỉ được khởi tạo một lần trong vòng đời backend. Chuyển trang trên giao diện không tải lại model hoặc mở lại camera.

## 4. Luồng khởi động

Khi chạy `python facecheck.py <camera>`:

1. Launcher gọi `http://127.0.0.1:8765/api/health` để bảo đảm không có phiên FaceCheck khác đang chạy.
2. Các biến trong `.env` được nạp, nhưng không ghi đè biến đã đặt từ shell.
3. Launcher cấu hình nguồn camera:
   - `webcam`: xóa URL stream và chọn camera index.
   - `usb`: mở `tools/usb_camera_bridge.py`, chờ stream nội bộ tại cổng `8766`.
   - `wifi`: tìm camera theo tham số CLI, `.env`, mDNS `facecheck-cam.local`, sau đó có thể quét các mạng IPv4 cục bộ; stream được lấy trực tiếp từ ESP32 tại cổng `81`.
4. `web_admin.py` giữ độc quyền cổng `8765` trước khi import backend. Việc này ngăn tiến trình thừa tải model hoặc mở camera.
5. Backend tạo một `WebFaceCheckRuntime`, tải YOLO/MediaPipe, nạp cache nhân viên, warm-up ArcFace ở background và mở camera.
6. Runtime mặc định chạy chế độ điểm danh.
7. Giao diện quản trị mở tại `http://127.0.0.1:8765/?view=admin#dashboard`.
8. Đồng thời, server chỉ đọc cho TFT lắng nghe ở cổng `8767`. Giao diện quản trị chính vẫn chỉ bind vào localhost.
9. Khi đóng cửa sổ chính, runtime dừng worker, giải phóng camera và đóng các server do tiến trình sở hữu.

## 5. Luồng điểm danh/nhận diện

### 5.1 Phát hiện và chọn khuôn mặt

Worker điểm danh đọc camera liên tục nhưng giới hạn YOLO theo chu kỳ cấu hình để UI vẫn mượt. Kết quả detect gần nhất được tái sử dụng giữa các lần inference.

Nếu có nhiều người, hệ thống chọn khuôn mặt có bounding box lớn nhất, tức người được giả định đang gần camera nhất. Khuôn mặt chính phải lớn hơn khuôn mặt thứ hai ít nhất theo tỷ lệ `KIOSK_PRIMARY_FACE_MIN_AREA_RATIO`; nếu không, UI yêu cầu người cần điểm danh tiến gần hơn.

Trước khi nhận diện, ứng viên phải thỏa các điều kiện chính:

- Bounding box đủ tin cậy và không bị cắt sát mép ảnh.
- Chiều rộng mặt đủ lớn cho ArcFace.
- Góc nghiêng, yaw và pitch trong giới hạn.
- Độ sáng không quá tối hoặc cháy sáng.
- Khuôn mặt tồn tại ổn định ít nhất `KIOSK_FACE_STABLE_SECONDS`.

### 5.2 Trích xuất và so khớp

1. Mặt được mở rộng thêm padding rồi căn chỉnh bằng landmark.
2. ArcFace sinh vector đặc trưng 512 chiều.
3. Runtime chuẩn hóa và trung bình embedding của nhiều frame liên tiếp để giảm nhiễu JPEG.
4. Query embedding được so với centroid embedding của từng hồ sơ đang bật nhận diện.
5. Các hồ sơ benchmark có `source = "LFW benchmark"` luôn bị loại khỏi nhận diện thực tế.
6. Kết quả được xếp theo cosine distance; giá trị càng nhỏ càng giống.
7. Hệ thống xét đồng thời:
   - khoảng cách của Top 1;
   - margin giữa Top 1 và Top 2;
   - số frame xác nhận;
   - khoảng thời gian tối thiểu giữa các frame.

### 5.3 Chính sách quyết định

- **Strict:** Top 1 nằm trong `ARCFACE_THRESHOLD` và cách Top 2 đủ xa theo `ARCFACE_TOP2_MARGIN`. Đây là nhánh duy nhất có quyền ghi điểm danh.
- **High confidence:** kết quả rất mạnh có thể đi theo nhánh xác nhận nhanh, nhưng vẫn cần hai frame độc lập và đủ khoảng thời gian.
- **Strict confirmation:** trường hợp sát ngưỡng phải tích lũy chuỗi xác nhận trong cửa sổ thời gian; frame nhiễu làm đứt chuỗi.
- **Ambiguous:** có nét tương đồng nhưng không đủ margin/độ tin cậy; hệ thống từ chối xác minh.
- **Unknown:** không có hồ sơ phù hợp; cần nhiều lần kiểm tra hợp lệ trước khi kết luận người lạ.

Sau kết quả cuối, UI giữ thông báo một khoảng ngắn. Khuôn mặt vừa xử lý bị tạm loại khỏi hàng đợi để một người đứng lại trước camera không chặn người tiếp theo.

### 5.4 Ghi lịch sử và chống trùng

- Kết quả thành công được ghi vào `data/attendance_history.json`.
- Kết quả mơ hồ/người lạ cũng được ghi với trạng thái thất bại và metric liên quan.
- Lịch sử trong runtime được giới hạn ở 100 bản ghi gần nhất.
- Cùng một nhân viên nhận diện lại trong `KIOSK_ATTENDANCE_COOLDOWN_SECONDS` sẽ hiện “Đã điểm danh trước đó” và không tạo bản ghi trùng.

## 6. Luồng đăng ký nhân viên

1. Quản trị viên mở trang **Đăng ký khuôn mặt**.
2. Frontend chuyển runtime sang mode `add_employee`; worker điểm danh dừng và worker đăng ký dùng lại camera/model hiện có.
3. Quản trị viên nhập mã nhân viên, họ tên, chức vụ và phòng ban rồi bấm **Bắt đầu quét tại kiosk**.
4. Camera kiểm tra chỉ có một khuôn mặt, vị trí, kích thước, YOLO confidence, MediaPipe confidence, góc đầu, độ sáng và vùng miệng không bị che.
5. State machine dùng chuỗi frame hợp lệ/không hợp lệ để tránh trạng thái nhấp nháy.
6. Khi mặt hợp lệ liên tục, hệ thống đếm ngược và thu tối đa 5 ảnh crop vào RAM, cách nhau theo `ENROLLMENT_SAMPLE_INTERVAL_SECONDS`.
7. Có ít nhất 3 mẫu hợp lệ thì snapshot được khóa và nút lưu được bật.
8. Khi lưu, từng mẫu được căn chỉnh và đưa qua ArcFace. Các vector được chuẩn hóa; centroid được lưu để tương thích, đồng thời giữ toàn bộ danh sách embedding.
9. Hệ thống kiểm tra trùng mã nhân viên không phân biệt hoa/thường trước khi ghi.
10. Một ảnh toàn frame được lưu trong `data/faces/`; hồ sơ và embedding được ghi nguyên tử vào `data/embeddings.pkl` qua file tạm.
11. Sau khi lưu, người cũ phải rời khỏi khung. Quản trị viên nhập hồ sơ mới và chủ động bắt đầu lượt tiếp theo.
12. Nếu phiên đăng ký không có thao tác trong 15 giây và không bận lưu/chụp, runtime tự trở về điểm danh.

Tên file ảnh có dạng:

```text
<ma_nv>@<chuc_vu>@<phong_ban>@<ho_ten>_<YYYYMMDD_HHMMSS>.jpg
```

## 7. Giao diện và các chế độ

Giao diện quản trị có các trang:

- **Tổng quan:** số hồ sơ, lượt hôm nay, tỷ lệ thành công, thống kê tuần và bản ghi gần nhất.
- **Đăng ký:** nhập thông tin, theo dõi chất lượng camera, quét và lưu khuôn mặt.
- **Giám sát nhận diện:** xem trạng thái camera/AI, kết quả gần nhất và kiểm thử bằng ảnh.
- **Nhân viên:** tìm kiếm, xem ảnh, sửa thông tin, bật/tắt tham gia nhận diện hoặc xóa hồ sơ.
- **Lịch sử:** tìm kiếm, lọc theo trạng thái và phân trang.

Hai kiểu hiển thị được hỗ trợ:

- `?view=admin`: giao diện quản trị đầy đủ.
- `?view=kiosk`: khóa router ở trang nhận diện, phù hợp màn hình kiosk.

Frontend nhận state qua `/ws/state` mỗi khoảng 350 ms và nhận camera qua `/api/camera/stream`. Nhiều client dùng chung JPEG cache để tránh encode cùng một frame nhiều lần.

## 8. Cấu trúc thư mục

```text
AI_RECOGNITION/
├── facecheck.py                 # Launcher webcam/USB/Wi-Fi
├── web_admin.py                 # Vòng đời server và cửa sổ desktop
├── web_runtime.py               # State, CRUD, callback và điều phối mode
├── web_backend/app.py           # REST API, WebSocket, static files
├── ai_engine.py                 # Detect, quality, alignment, distance
├── camera_capture.py            # OpenCV/local camera và latest-frame MJPEG
├── ekyc_renderer.py             # Overlay/HUD đăng ký
├── config.py                    # Ngưỡng AI, camera và timing
├── mixins/
│   ├── attendance.py            # Pipeline điểm danh
│   ├── camera.py                # Pipeline camera đăng ký
│   ├── enrollment.py            # Tạo/lưu hồ sơ khuôn mặt
│   └── core.py                  # Model, cache, lịch sử
├── web_ui/
│   ├── index.html
│   ├── css/                     # Layout, component, kiosk, animation
│   └── js/                      # API bridge, router, state, app
├── models/
│   ├── model.pt                 # YOLO face detector
│   └── blaze_face_short_range.tflite
├── data/                        # Dữ liệu runtime, không commit Git
├── tools/                       # Bridge, benchmark và hiệu chuẩn
├── firmware/
│   ├── esp32_s3_ov3660/         # Camera Wi-Fi
│   ├── esp32_s3_ov3660_usb/     # Camera USB-OTG
│   └── esp32_035_display/       # Màn hình TFT
└── tests/                       # Unit/smoke tests
```

## 9. Yêu cầu hệ thống

- Windows 10/11 được hỗ trợ trực tiếp bởi launcher và cấu hình camera hiện tại.
- Python 3.11 khuyến nghị; môi trường phát triển hiện tại dùng Python 3.11.4.
- Webcam hoặc một ESP32-S3 WROOM CAM + OV3660 đã nạp firmware phù hợp.
- RAM đủ để chạy YOLO, MediaPipe, TensorFlow/DeepFace; GPU CUDA là tùy chọn.
- Nếu dùng ESP32 Wi-Fi/TFT: các thiết bị và laptop phải liên lạc được trong cùng LAN 2.4 GHz, hoặc dùng SoftAP theo cấu hình firmware.
- Nếu build firmware: VS Code + PlatformIO, hoặc Arduino IDE 2.x đối với firmware camera.

`check_gpu_quick()` tự chọn CUDA khi khả dụng, nếu không hệ thống chạy bằng CPU.

## 10. Cài đặt

Tại thư mục gốc dự án:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Mã nguồn import trực tiếp `mediapipe`. Nếu môi trường mới báo `ModuleNotFoundError: mediapipe` sau bước trên, cài bổ sung:

```powershell
pip install mediapipe
```

Cài thêm dependency phục vụ kiểm thử:

```powershell
pip install -r requirements-dev.txt
```

Hai model sau phải tồn tại trước khi chạy AI:

```text
models/model.pt
models/blaze_face_short_range.tflite
```

Ở lần dùng ArcFace đầu tiên, DeepFace có thể cần chuẩn bị trọng số model trong cache người dùng. Hãy khởi động lần đầu khi máy có thể hoàn tất bước này.

## 11. Cấu hình `.env`

Tạo `.env` tại thư mục gốc nếu chưa có. Không commit file này vì chứa địa chỉ mạng và token.

Ví dụ an toàn:

```dotenv
# Camera Wi-Fi ưu tiên; có thể dùng IP hoặc hostname
FACECHECK_CAMERA_HOST=facecheck-cam.local

# Profile: webcam hoặc embedded_ov3660
FACECHECK_CAMERA_PROFILE=webcam

# 1 = lật gương, 0 = giữ nguyên
FACECHECK_CAMERA_FLIP=1

# Địa chỉ LAN của laptop để TFT truy cập, ví dụ 192.168.1.11
FACECHECK_DISPLAY_HOST=192.168.1.11
FACECHECK_DISPLAY_TOKEN=THAY_BANG_TOKEN_DAI_NGAU_NHIEN
# Dùng cho WebSocket camera bridge thử nghiệm
FACECHECK_CAMERA_TOKEN=THAY_BANG_TOKEN_CAMERA_KHAC

# Điều khiển cửa ESP32-S3 qua HiveMQ Cloud
MQTT_HOST=THAY_BANG_HOST_HIVEMQ
MQTT_PORT=8883
MQTT_USERNAME=THAY_BANG_TAI_KHOAN
MQTT_PASSWORD=THAY_BANG_MAT_KHAU
MQTT_DOOR_DEVICE_ID=door-01
FACECHECK_DOOR_ENABLED=1
FACECHECK_DOOR_OPEN_DURATION_MS=3000
FACECHECK_DOOR_COOLDOWN_SECONDS=8
FACECHECK_DOOR_FACE_EXIT_SECONDS=1.5
FACECHECK_DOOR_ACK_TIMEOUT_SECONDS=12

# Tùy chọn preview/bridge
FACECHECK_PREVIEW_WIDTH=640
FACECHECK_PREVIEW_QUALITY=80
```

Các biến runtime đáng chú ý:

- `FACECHECK_CAMERA_INDEX`: camera local ưu tiên, mặc định `0`; runtime thử tiếp `0`, `1`, `2` nếu cần.
- `FACECHECK_CAMERA_URL`: URL MJPEG. Launcher tự đặt cho mode USB/Wi-Fi; thường không cần nhập tay.
- `FACECHECK_CAMERA_PROFILE`: `webcam` hoặc `embedded_ov3660`; profile nhúng dùng ngưỡng đăng ký phù hợp ảnh nén.
- `FACECHECK_CAMERA_FLIP`: bật/tắt lật ngang.
- `FACECHECK_DRAW_DEBUG_BOXES=1`: vẽ bounding box khi chẩn đoán detector.
- `FACECHECK_DISPLAY_HOST`: IP laptop được in cho màn hình TFT.
- `FACECHECK_DISPLAY_TOKEN`: token bắt buộc cho API TFT ở cổng `8767`.
- Quản lý vân tay dùng trực tiếp trên dịch vụ quản trị localhost `127.0.0.1:8765`, không yêu cầu mã mở khóa riêng. Không công khai cổng quản trị ra LAN khi chưa bổ sung đăng nhập toàn hệ thống.
- `FACECHECK_DOOR_ENABLED`: `1` để tự mở cửa sau nhận diện thành công; đặt `0` để tắt mà không xóa thông tin broker.
- `FACECHECK_DOOR_OPEN_DURATION_MS`: thời gian giữ relay, firmware giới hạn trong 500–10000 ms.
- `FACECHECK_DOOR_COOLDOWN_SECONDS`: thời gian nghỉ toàn cửa sau ACK `finished`, mặc định 8 giây.
- `FACECHECK_DOOR_FACE_EXIT_SECONDS`: camera phải không thấy khuôn mặt liên tục trong thời gian này mới re-arm, mặc định 1,5 giây.
- `FACECHECK_DOOR_ACK_TIMEOUT_SECONDS`: thời gian tối đa chờ ESP32 hoàn tất lệnh, mặc định 12 giây.
- `FACECHECK_WEB_SKIP_AI=1`: chỉ dùng cho test/smoke test; bỏ qua tải model và mở camera.

Biến môi trường đặt từ PowerShell có độ ưu tiên cao hơn giá trị trong `.env`.

## 12. Chạy hệ thống

### ESP32-S3 Wi-Fi mặc định

```powershell
python facecheck.py
```

Lệnh không có tham số mặc định tìm camera ESP32-S3 theo
`FACECHECK_CAMERA_HOST`, sau đó thử mDNS `facecheck-cam.local` và quét LAN nếu
cần. Nó không tự rơi về webcam máy tính.

### Webcam máy tính

```powershell
python facecheck.py webcam
```

Hoặc chọn camera cụ thể:

```powershell
python facecheck.py webcam --camera-index 1
```

Webcam local được yêu cầu MJPG, 640×480, 30 FPS và buffer size 1. Driver có thể trả về thông số thực tế khác tùy thiết bị.

### ESP32-S3 qua USB-OTG

```powershell
python facecheck.py usb
```

Chỉ định cổng CDC nếu tự động dò không đúng:

```powershell
python facecheck.py usb --usb-port COM13
```

Bridge đọc JPEG từ serial và cung cấp stream nội bộ:

- `http://127.0.0.1:8766/stream`
- `http://127.0.0.1:8766/capture`
- `http://127.0.0.1:8766/health`

### ESP32-S3 qua Wi-Fi

```powershell
python facecheck.py wifi
```

Chỉ định IP/hostname để bỏ qua sự không chắc chắn của mDNS:

```powershell
python facecheck.py wifi --camera-host 192.168.1.105
```

Các tùy chọn:

```powershell
python facecheck.py wifi --no-lan-scan
```

Launcher cũng giữ tùy chọn tương thích `--skip-fps-check`; ở phiên bản hiện tại phép đo FPS lúc khởi động đã được tắt mặc định nên tùy chọn này không làm thay đổi hành vi.

Luồng hiện tại lấy MJPEG trực tiếp từ `http://<camera-ip>:81/stream`. ESP32 cung cấp health tại `http://<camera-ip>/health` để launcher xác minh đúng loại camera.

### Mô phỏng màn hình kiosk

Thêm `--simulate-display` hoặc alias `--simulate-pi` vào bất kỳ mode nào:

```powershell
python facecheck.py wifi --simulate-display
```

Ứng dụng mở đồng thời một cửa sổ kiosk 480×320 và một cửa sổ quản trị 1440×900.

> Chỉ chạy một phiên FaceCheck tại một thời điểm. Muốn đổi nguồn camera, hãy đóng ứng dụng hiện tại rồi chạy lại launcher.

## 13. Các cổng mạng

- `127.0.0.1:8765`: backend, Admin UI, Kiosk UI, REST, WebSocket state và camera stream. Chỉ truy cập cục bộ.
- `0.0.0.0:8767`: API ảnh/trạng thái chỉ đọc cho TFT. Yêu cầu header `X-FaceCheck-Token` và nên chỉ cho phép trong LAN tin cậy.
- `127.0.0.1:8766`: USB camera bridge khi chạy mode `usb`.
- `0.0.0.0:8768`: đầu vào WebSocket của camera bridge thử nghiệm.
- `127.0.0.1:8769`: MJPEG đầu ra nội bộ của WebSocket/MQTT camera bridge.
- `<ESP32-IP>:80`: trang chẩn đoán, `/health`, `/capture` của camera Wi-Fi.
- `<ESP32-IP>:81`: MJPEG `/stream` của camera Wi-Fi.

## 14. API nội bộ

Các endpoint chính trên cổng `8765`:

- `GET /api/health`: kiểm tra đúng backend FaceCheck.
- `GET /api/state`: snapshot runtime hiện tại.
- `POST /api/mode`: chuyển giữa `attendance`, `add_employee`, `idle`.
- `GET /api/camera/stream`: MJPEG dùng cho UI.
- `WS /ws/state`: stream snapshot trạng thái.
- `POST /api/recognition/retry`: mở lại lượt nhận diện.
- `POST /api/recognition/test-image`: kiểm thử file ảnh; tên file truyền qua header `X-Filename`.
- `POST /api/enrollment/start`: bắt đầu lượt quét đăng ký.
- `POST /api/enrollment/save`: lưu hồ sơ đã quét.
- `POST /api/enrollment/reset`: hủy snapshot đăng ký.
- `POST /api/enrollment/activity`: gia hạn timeout phiên đăng ký.
- `POST /api/enrollment/finish`: kết thúc phiên và quay lại điểm danh.
- `GET /api/employees`: danh sách/tìm kiếm/phân trang nhân viên.
- `GET /api/employees/{id}/image`: ảnh hồ sơ.
- `PUT /api/employees/{id}`: sửa thông tin.
- `POST /api/employees/{id}/toggle`: bật/tắt nhận diện.
- `DELETE /api/employees/{id}`: xóa hồ sơ và ảnh.
- `GET /api/attendance`: lịch sử có lọc và phân trang.
- `GET /api/dashboard`: số liệu tổng quan.

API TFT trên cổng `8767` chỉ có:

- `GET /health`
- `GET /frame.jpg`

Cả hai yêu cầu `X-FaceCheck-Token` trùng với `FACECHECK_DISPLAY_TOKEN`.

## 15. Dữ liệu lưu trữ

- `data/embeddings.pkl`: dictionary hồ sơ nhân viên và vector sinh trắc học.
- `data/faces/*.jpg`: một ảnh tham chiếu/hiển thị cho mỗi hồ sơ đăng ký trực tiếp.
- `data/attendance_history.json`: tối đa 100 sự kiện gần nhất do runtime duy trì.
- `data/benchmark_faces/`: dữ liệu và báo cáo benchmark offline.
- `data/captures/`, `data/known_faces/`: dữ liệu hỗ trợ hiệu chuẩn/kiểm thử nếu có.

`data/`, `models/`, `.env` và các file secret firmware đều bị `.gitignore` loại trừ. Khi chuyển máy, cần sao lưu riêng tối thiểu:

```text
data/embeddings.pkl
data/faces/
data/attendance_history.json
models/
.env
```

`embeddings.pkl` và ảnh khuôn mặt là dữ liệu sinh trắc học nhạy cảm. Hãy giới hạn quyền đọc, mã hóa bản sao lưu, không đưa vào repository và tuân thủ quy định bảo vệ dữ liệu áp dụng tại nơi triển khai.

Không chỉnh sửa `embeddings.pkl` thủ công. Khi khởi động, cache sẽ loại vector mồ côi nếu không tìm thấy ảnh tương ứng trong `data/faces/`.

## 16. Firmware

### Bộ điều khiển cửa/vân tay — `firmware/Smart_Door`

ESP32-S3 nhận `OPEN` qua MQTT/TLS nhưng callback mạng không điều khiển relay trực tiếp. Lệnh hợp lệ được đưa vào FreeRTOS queue và chỉ `Task_LockControl` được phép mở khóa. Mỗi lệnh có `command_id`, TTL, giới hạn thời gian mở và ACK `started`/`finished`/`failed`. Khi mất mạng, vân tay và nút thoát vẫn mở cửa cục bộ.

Server chỉ phát một lệnh tại một thời điểm. Sau khi hoàn tất, nó áp dụng cooldown và yêu cầu camera không còn khuôn mặt liên tục trước khi cho phép lượt mở tiếp theo. Nếu mất ACK, server giả định relay có thể đã hoạt động và chuyển sang trạng thái khóa lặp an toàn.

### Camera Wi-Fi — `firmware/esp32_s3_ov3660`

1. Sao chép `secrets.example.h` thành `secrets.h`.
2. Điền Wi-Fi 2.4 GHz hoặc bật SoftAP.
3. Build/upload bằng PlatformIO environment `esp32-s3-wroom-cam`.
4. Theo dõi serial ở 115200 baud.
5. Kiểm tra `/health`, `/capture` và stream cổng `81`.

Firmware hiện ưu tiên mô hình laptop kéo MJPEG trực tiếp; code WebSocket push vẫn còn để thử nghiệm nhưng `kWebSocketPushEnabled = false`.

### Camera USB — `firmware/esp32_s3_ov3660_usb`

Firmware gửi JPEG từ OV3660 qua USB-OTG. Sau khi nạp, Windows tạo cổng CDC runtime; `tools/usb_camera_bridge.py` đọc frame và chuyển thành MJPEG local.

Lưu ý cổng upload và cổng CDC runtime có thể khác nhau. Xem Device Manager và log bridge thay vì giả định cố định theo ví dụ trong `platformio.ini`.

### TFT — `firmware/esp32_035_display`

1. Sao chép `secrets.example.h` thành `include/secrets.h`.
2. Điền Wi-Fi, URL `http://<IP-laptop>:8767` và cùng `FACECHECK_DISPLAY_TOKEN` với `.env`.
3. Thử environment `esp32-035-st7796`; nếu màn hình trắng/đen/nhiễu, thử `esp32-035-ili9488`.
4. Cho phép inbound TCP 8767 trên Windows Firewall trong mạng private.

TFT chỉ tải JPEG và đọc trạng thái từ laptop; thiết bị không chạy AI và không kết nối trực tiếp với camera.
Ở chế độ chấm công, backend tạo JPEG 480×250 phủ kín vùng ảnh phía trên;
camera gốc, frame AI và chế độ đăng ký 480×320 không đổi.
Kênh hiển thị chạy 5 FPS với pipeline latest-frame hai buffer: Wi-Fi/TCP của
ESP-IDF xử lý ở Core 0, còn task HTTP và task vẽ LovyanGFX được điều phối ở
Core 1. Queue chỉ giữ một frame sẵn sàng nên frame cũ không thể tích backlog.
Request LAN dùng timeout ngắn và chỉ retry một lần trên socket mới; log TFT
hiển thị `DROP`, `RETRY`, `FAIL` cùng thời gian trung bình/tối đa để phân biệt
chậm mạng với chậm giải mã/vẽ.

Mỗi thư mục firmware có README riêng với hướng dẫn phần cứng chi tiết.

## 17. Công cụ hỗ trợ

- `tools/usb_camera_bridge.py`: serial JPEG → MJPEG local cho mode USB.
- `tools/ws_camera_bridge.py`: nhận binary JPEG từ ESP32 qua WebSocket có token → MJPEG local.
- `tools/mqtt_camera_bridge.py`: MQTT JPEG → MJPEG local; cần cài thêm client MQTT tương ứng nếu sử dụng.
- `tools/mqtt_door_monitor.py`: theo dõi heartbeat/status của bộ điều khiển cửa.
- `tools/mqtt_door_ping.py`: kiểm tra MQTT hai chiều mà không kích relay.
- `tools/mqtt_door_open.py`: gửi `OPEN` thủ công, bắt buộc có `--confirm-open`.

Sự kiện cục bộ từ bộ điều khiển cửa được nhận tại
`smartdoor/v1/<device_id>/event`, lọc theo whitelist rồi lưu tối đa 100 bản ghi
trong `data/door_events.json`. API đọc là `GET /api/door/events`; snapshot
WebSocket cũng chứa trạng thái heartbeat, AS608 và sự kiện gần nhất. Không có
tên, ảnh hay template vân tay trong payload MQTT.
- `tools/read_serial.py`: đọc log serial trong một khoảng thời gian.
- `tools/benchmark_wifi_camera.py`: đo nhiều mức FPS camera Wi-Fi và chọn mức ổn định.
- `tools/prepare_lfw_benchmark.py`: chuẩn bị bộ đánh giá LFW.
- `tools/import_lfw_benchmark.py`: nhập hồ sơ benchmark, mặc định không cho tham gia nhận diện thật.
- `tools/select_lfw_medoid_gallery.py`: chọn ảnh đại diện medoid cho gallery LFW.
- `tools/calibrate_recognition.py`: đánh giá/nghiệm thu ngưỡng ArcFace chung.
- `tools/calibrate_esp_camera_recognition.py`: hiệu chuẩn trên ảnh từ camera ESP.

Xem `--help` trước khi chạy một công cụ:

```powershell
python tools\benchmark_wifi_camera.py --help
python tools\calibrate_recognition.py --help
```

## 18. Kiểm thử

Chạy toàn bộ test mà không tải model hoặc mở camera thật:

```powershell
$env:FACECHECK_WEB_SKIP_AI="1"
python -m unittest discover -s tests -v
```

Bộ test hiện kiểm tra các nhóm chính:

- camera capture/latest frame và bridge WebSocket;
- launcher, dò camera Wi-Fi và chọn nguồn camera;
- API/UI smoke test và vòng đời single-instance;
- chính sách nhận diện nhiều frame, centroid và chống spike nhiễu;
- phiên đăng ký liên tục, timeout và yêu cầu người cũ rời camera;
- chất lượng khuôn mặt, độ sáng và vùng miệng;
- layout eKYC và lựa chọn FPS camera.

Sau test có thể xóa biến môi trường trong phiên PowerShell:

```powershell
Remove-Item Env:FACECHECK_WEB_SKIP_AI -ErrorAction SilentlyContinue
```

## 19. Điều chỉnh nhận diện

Các ngưỡng quan trọng nằm trong `config.py`:

- `ARCFACE_THRESHOLD`, `ARCFACE_TOP2_MARGIN`: điều kiện nhận diện strict.
- `ARCFACE_HIGH_CONFIDENCE_*`: nhánh xác nhận nhanh.
- `ARCFACE_STRICT_*`: số phiếu, số lần thử và cửa sổ thời gian.
- `KIOSK_MIN_FACE_WIDTH`, `KIOSK_MIN_FACE_CONFIDENCE`: chất lượng ứng viên.
- `KIOSK_*_ANGLE/RATIO`: giới hạn tư thế.
- `FACE_MIN_BRIGHTNESS`, `FACE_MAX_BRIGHTNESS`: giới hạn ánh sáng.
- `ENROLLMENT_*`: chất lượng, số mẫu và timing đăng ký.
- `KIOSK_ATTENDANCE_COOLDOWN_SECONDS`: thời gian chống ghi trùng.

Không nên giảm threshold theo cảm tính. Quy trình an toàn là chuẩn bị tập known/unknown đại diện đúng camera triển khai, chạy công cụ calibration, xem false accept/false reject rồi thay đổi một nhóm ngưỡng tại một thời điểm.

## 20. Xử lý sự cố

### Không tìm thấy camera webcam

- Đóng ứng dụng khác đang giữ camera.
- Thử `python facecheck.py webcam --camera-index 1` rồi index `2`.
- Kiểm tra quyền Camera trong Windows Settings.
- Xem log `[CAMERA] Nguồn ưu tiên` và `[CAMERA] KHÔNG TÌM THẤY CAMERA`.

### Không tìm thấy camera Wi-Fi

- Bảo đảm laptop và ESP32 ở cùng mạng và client isolation không chặn thiết bị LAN.
- Mở `http://<camera-ip>/health` trên laptop.
- Chạy với `--camera-host <ip>`.
- Nếu dùng SoftAP, kết nối laptop vào SSID của camera trước khi chạy.
- Kiểm tra serial monitor 115200 để lấy IP thật.

### Camera USB không lên

- Dùng đúng cổng USB-OTG/CDC runtime, không nhầm với USB-UART dùng monitor/upload.
- Kiểm tra Device Manager và truyền `--usb-port COMx`.
- Bảo đảm không có serial monitor khác đang giữ cổng.
- Mở `http://127.0.0.1:8766/health` để kiểm tra bridge.

### Cổng 8765 hoặc 8767 bị chiếm

- Đóng phiên FaceCheck cũ.
- Kiểm tra process đang lắng nghe cổng bằng `Get-NetTCPConnection -LocalPort 8765,8767`.
- Không khởi chạy trực tiếp nhiều `web_admin.py`; launcher đã có kiểm tra single-instance.

### Lần nhận diện đầu chậm

- ArcFace được warm-up trong background, nhưng lần đầu trên máy mới có thể cần tải/khởi tạo trọng số.
- Đợi log `[AI] DeepFace ArcFace đã sẵn sàng`.
- CPU-only sẽ chậm hơn CUDA; không phải lỗi nếu camera vẫn hoạt động.

### Nhận diện nhầm hoặc từ chối nhiều

- Đặt mặt đủ gần, nhìn thẳng và chiếu sáng đều.
- Đăng ký lại bằng đúng camera/môi trường sẽ dùng khi điểm danh.
- Không dùng ảnh mờ, cháy sáng hoặc che miệng.
- Chạy calibration thay vì chỉ sửa `ARCFACE_THRESHOLD`.
- Bật `FACECHECK_DRAW_DEBUG_BOXES=1` tạm thời để chẩn đoán vùng detect.

### TFT báo Forbidden hoặc không kết nối

- Token trong firmware phải giống hệt `FACECHECK_DISPLAY_TOKEN` trong `.env`.
- `FACECHECK_SERVER` phải dùng IPv4 của laptop, không dùng `127.0.0.1`.
- Cho phép cổng 8767 trong Windows Firewall cho mạng private.
- Nếu IP laptop thay đổi, cập nhật firmware hoặc cấp DHCP reservation.

## 21. Ghi chú vận hành và bảo mật

- Admin backend chỉ bind `127.0.0.1`; không đổi sang `0.0.0.0` nếu chưa bổ sung xác thực.
- Kênh TFT bind LAN nhưng chỉ đọc và bắt buộc token. Dùng token dài, ngẫu nhiên và khác token camera.
- Không commit `.env`, `secrets.h`, ảnh mặt, embedding hay lịch sử thực tế.
- Sao lưu ảnh và `embeddings.pkl` cùng nhau; thiếu ảnh, runtime có thể dọn embedding tương ứng như dữ liệu mồ côi.
- Xóa nhân viên từ UI sẽ xóa hồ sơ embedding và ảnh, không thể hoàn tác nếu không có backup.
- Thời gian ghi nhận dùng đồng hồ máy tính; nên bật đồng bộ thời gian Windows.
- Nhật ký ứng dụng có thể chứa mã nhân viên, tên và đường dẫn ảnh; bảo vệ thư mục log khi triển khai thật.

## 22. Bắt đầu nhanh

```powershell
# 1. Kích hoạt môi trường
.\venv\Scripts\Activate.ps1

# 2. Cài dependency
pip install -r requirements.txt

# 3. Chạy với webcam
python facecheck.py webcam --camera-index 0

# 4. Chạy test ở một terminal khác khi ứng dụng đã đóng
$env:FACECHECK_WEB_SKIP_AI="1"
python -m unittest discover -s tests -v
```

Khi hệ thống chạy, cửa sổ quản trị mở tự động. Nếu PyWebView không khả dụng, chương trình sẽ mở URL tương ứng trong trình duyệt mặc định.
