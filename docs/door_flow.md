# Xác thực người xếp hàng và điều khiển khóa

Sau một lệnh mở khóa đã được ESP32 xác nhận hoàn tất, người kế tiếp không cần
đợi toàn bộ ảnh camera trống. Lệnh kế tiếp vẫn phải chờ chu kỳ trước kết thúc
và hết `FACECHECK_DOOR_COOLDOWN_SECONDS` (mặc định 8 giây sau ACK hoàn tất).

Server giữ chốt chống lặp riêng theo mã nhân viên. Chốt được liên kết với vị trí
khuôn mặt ở vùng xác thực gần camera. Người đó vẫn ở vị trí này thì không gửi
OPEN lặp, kể cả đã hết cooldown. Khi người đó rời vùng đủ thời gian
`FACECHECK_DOOR_FACE_EXIT_SECONDS` (mặc định 1,5 giây), chốt được bỏ. Nếu một
danh tính khác đã được AI xác minh tiếp quản cùng vị trí, server cũng có thể
xác nhận người trước không còn ở vị trí đó mà không cần nền ảnh trống.

Đây là kết hợp danh tính đã xác minh và vị trí khung mặt, không phải cảm biến
đếm người đi qua cửa. Khung hình nhiều mặt gần ngang nhau chưa xác định rõ
người phía trước thì giữ chốt thận trọng; cần từng người tiến vào vùng xác thực.

Một lượt đã xác thực trong lúc khóa bận chỉ hiển thị chờ. Không lưu một lệnh
mở tự động để thực hiện sau khi người đã đi. Camera tiếp tục hoạt động; sau
khoảng 4 giây có thể xác thực mới, và chỉ kết quả mới mới yêu cầu OPEN.

Nếu mất ACK, kết quả điều khiển khóa chưa chắc chắn: vẫn giữ cơ chế fail-closed,
chờ cooldown và ảnh camera trống trước khi cho phép lệnh tiếp theo. Không tự
gửi lại OPEN. Lệnh MQTT vẫn có command ID, hạn dùng và không được retain.

## Thông báo

- Đã xác thực / chờ xác nhận: server đã gửi yêu cầu, thiết bị chưa xác nhận.
- Đã xác thực / chờ khóa: thiết bị đang xử lý hoặc đang cooldown.
- Đang thực hiện mở khóa: ACK started của đúng command ID.
- Đã thực hiện mở khóa: ACK finished của đúng command ID.
- Đã xử lý lượt của bạn: chặn mở lặp của cùng người còn tại vùng xác thực.
- Đã xác thực / chưa xác nhận mở khóa: thiết bị mất kết nối hoặc lệnh thất bại.

OLED trên ESP32 xử lý hiển thị các thông báo ngắn tương ứng. Không cần nạp lại
ESP32 camera cho thay đổi này. Không có cảm biến vị trí cửa nên không khẳng định
cánh cửa đã mở/đóng vật lý chỉ từ ACK điều khiển relay.
