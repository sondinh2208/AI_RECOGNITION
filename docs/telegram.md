# Telegram trong FaceTouch Pro

ESP32 xử lý là nơi duy nhất nhận lệnh bot. Server chỉ gửi cảnh báo khuôn mặt;
không chạy một bộ nhận lệnh Telegram thứ hai với cùng token.

## Trên ESP32

- `/help`, `/status`: trợ giúp và trạng thái điều khiển khóa/cảm biến/MQTT.
- `/open` rồi `/open confirm` trong 30 giây: mở khóa từ xa, không cần server.
- `/cancel`: hủy yêu cầu đang chờ. Lệnh cũ hơn 30 giây bị bỏ qua.
- Chỉ nhận tin nhắn riêng từ admin và các tài khoản đã được ủy quyền trong NVS.
- Đăng ký, xóa vân tay, đặt tên và quản lý người dùng không còn thực hiện qua bot.
- Báo 3 lượt vân tay sai trong 60 giây, tối đa một cảnh báo/phút, chỉ gửi admin.
  Phải nhấc ngón tay trước khi tính lượt mới. Quét đúng xóa bộ đếm lỗi.
- Không gửi tin cho mỗi lượt quét đúng hoặc nhấn nút mở từ bên trong.
- MQTT task quản lý Wi-Fi/NTP; mất broker không ngăn Telegram chạy khi còn Internet.
- `/status` phản ánh điều khiển relay, không xác nhận vị trí vật lý cánh cửa.

Token và admin đặt trong `firmware/Smart_Door/secrets.h`, không commit file này.
Đổi firmware cần nạp lại ESP32 xử lý, không phải ESP32 camera.

## Cảnh báo ảnh khuôn mặt trên server

Trong `.env` (đã được Git bỏ qua), đặt:

```dotenv
FACECHECK_TELEGRAM_FACE_ALERTS=1
FACECHECK_TELEGRAM_BOT_TOKEN=TOKEN_CUA_CUNG_BOT_TREN_ESP32
FACECHECK_TELEGRAM_CHAT_ID=CHAT_ID_ADMIN_DA_XAC_NHAN
```

Khởi động lại server sau khi thay cấu hình. Thiếu cấu hình hoặc đặt cờ bằng 0
thì không gửi ảnh. Có thể xem `telegram` trong `/api/state` để biết trạng thái
bật/tắt, số lần gửi thành công/thất bại và số việc đang chờ. Không trả token lên API.

- Đạt **3** lượt xác thực cuối cùng thất bại trong 60 giây thì gửi một ảnh crop
  khuôn mặt của lượt thứ ba, kèm thời gian và thông báo chưa xác minh danh tính.
- Không tính lỗi chất lượng ảnh, từng frame video hoặc ảnh tải lên để kiểm thử.
- Sau một kết quả thất bại, chờ tối thiểu bốn giây trước lượt thử tiếp theo;
  không cần rời camera. Mỗi lượt vẫn phải đủ khung xác nhận và đạt kiểm tra chất
  lượng. Các callback lặp trong bốn giây không làm tăng bộ đếm cảnh báo.
  Khi có nhiều người, đây là bộ đếm tại cửa, không khẳng định các lượt thất bại
  thuộc cùng một người. Cơ chế chống mở khóa lặp sau xác thực thành công giữ nguyên.
- Xác thực đúng xóa bộ đếm lỗi. Tối đa một cảnh báo/phút.
- Giữ ảnh tạm trong RAM, không tạo file ảnh cảnh báo trên máy tính. JPEG mã hóa
  trong worker nền, giữ tối đa hai tác vụ chờ. Việc quá 60 giây bị bỏ.
- Gửi ảnh qua HTTPS `sendPhoto`. Không tự gửi lại sau timeout để tránh ảnh trùng;
  lỗi gửi không chặn việc nhận diện hoặc mở khóa. Ảnh đã gửi nằm trong Telegram.
- Chỉ ESP32 cảnh báo vân tay; server không gửi trùng sự kiện vân tay.

Telegram có giới hạn khi mất Internet. Server tắt thì không còn cảnh báo ảnh
khuôn mặt; vân tay offline và bot ESP32 vẫn độc lập với server.

Kiểm thử tự động dùng ảnh giả trong RAM và giả lập HTTP, không gửi ảnh người thật
hay thực hiện lệnh mở khóa.
