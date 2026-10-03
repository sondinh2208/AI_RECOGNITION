"""
============================================
KIOSK CAMERA - Nhận diện khuôn mặt
============================================
Dự án: Nhận diện khuôn mặt Kiosk
Mô tả: Mở camera, vẽ khung định vị trung tâm,
        dùng YOLOv8-Face detect khuôn mặt, kiểm tra
        khuôn mặt có nằm gọn trong khung hay không.
        
Quy tắc màu sắc:
  🔴 ĐỎ   -> Chưa phát hiện / mặt nằm ngoài khung
  🟢 XANH -> Mặt nằm gọn 100% trong khung (KHÓA MỤC TIÊU)

AI Models:
  - YOLOv8-Face (arnabdhar/YOLOv8-Face-Detection) → Detect khuôn mặt
  - YOLOv8n (COCO) → Detect person (thông tin phụ)
GPU: NVIDIA CUDA (RTX 40-series)
============================================
"""

import cv2
import mediapipe as mp
from config import KIOSK_WINDOW_NAME
from ai_engine import (
    check_gpu,
    load_face_model, load_person_model, load_mediapipe_detector,
    detect_faces, detect_persons,
    check_face_constraints,
)
from ekyc_renderer import (
    compute_ekyc_layout, create_rounded_rect_mask,
    render_full_hud,
)


def init_camera():
    """
    Khởi tạo camera với DirectShow và MJPG 30 FPS.
    Thử camera ID = 1 trước (camera ngoài), nếu lỗi lùi về ID = 0.
    """
    for cam_id in [1, 0]:
        print(f"[INFO] Đang thử mở camera ID = {cam_id}...")
        for backend in [cv2.CAP_DSHOW, cv2.CAP_ANY]:
            cap = cv2.VideoCapture(cam_id, backend) if backend != cv2.CAP_ANY else cv2.VideoCapture(cam_id)
            if not cap.isOpened():
                continue
            
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            ret, frame = cap.read()
            if ret and frame is not None:
                print(f"[OK] Đã kết nối camera ID = {cam_id} ({frame.shape[1]}x{frame.shape[0]} @ 30 FPS MJPG) thành công!")
                return cap
            else:
                cap.release()
        print(f"[WARN] Không thể mở camera ID = {cam_id}.")
    
    print("[FATAL] Không tìm thấy camera nào khả dụng! Thoát chương trình.")
    exit(1)


def main():
    """
    Hàm chính - Vòng lặp camera chính của Kiosk.
    GPU-Accelerated với NVIDIA CUDA.
    """
    print("=" * 50)
    print("  KIOSK - HỆ THỐNG NHẬN DIỆN KHUÔN MẶT")
    print("  YOLOv8-Face + GPU-Accelerated (CUDA)")
    print("=" * 50)
    print()
    
    # --- Bước 0: Kiểm tra GPU/CUDA ---
    device = check_gpu()
    
    # --- Bước 1: Khởi tạo camera ---
    cap = init_camera()
    
    # --- Bước 2: Load models ---
    face_model, device = load_face_model(device)
    person_model, device = load_person_model(device)
    face_detector = load_mediapipe_detector()
    
    # --- Bước 3: Lấy kích thước frame ---
    ret, test_frame = cap.read()
    if not ret:
        print("[ERROR] Không đọc được frame đầu tiên!")
        exit(1)
    
    frame_height, frame_width = test_frame.shape[:2]
    
    # --- Bước 4: Tính layout eKYC + tạo mask ---
    shape_rect, corner_radius = compute_ekyc_layout(frame_width, frame_height)
    constraint_box = shape_rect
    ekyc_mask = create_rounded_rect_mask(frame_width, frame_height, shape_rect, corner_radius)
    
    print(f"[INFO] Kích thước camera: {frame_width}x{frame_height}")
    print(f"[INFO] Shape rect: {shape_rect}, corner_r={corner_radius}")
    print(f"[INFO] Device inference: {device}")
    print()
    print("[INFO] Nhấn 'Q' hoặc 'ESC' để thoát.")
    print("-" * 50)
    
    prev_status = None
    
    # ============================================
    # VÒNG LẶP CAMERA CHÍNH
    # ============================================
    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Mất kết nối camera!")
            break
        
        frame = cv2.flip(frame, 1)
        
        # --- AI Inference ---
        faces = detect_faces(face_model, frame, device)
        persons = detect_persons(person_model, frame, device)
        
        # --- MediaPipe landmarks ---
        mp_results = None
        if face_detector is not None:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            mp_results = face_detector.detect(mp_image)
        
        # --- Kiểm tra ràng buộc ---
        current_color, status_text, is_locked = check_face_constraints(
            faces, constraint_box, mp_results, frame_width, frame_height
        )
        
        # --- Log terminal ---
        if status_text != prev_status:
            icon = "✅ XANH" if is_locked else "🔴 ĐỎ"
            print(f"[{icon}] {status_text}")
            prev_status = status_text
        
        # --- Render HUD ---
        output = render_full_hud(
            frame, ekyc_mask, shape_rect, current_color,
            status_text, frame_width, frame_height
        )
        
        cv2.imshow(KIOSK_WINDOW_NAME, output)
        
        # --- Phím bấm ---
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == ord('Q') or key == 27:
            print("\n[INFO] Người dùng nhấn thoát. Đang đóng chương trình...")
            break
    
    # ============================================
    # DỌN DẸP TÀI NGUYÊN
    # ============================================
    cap.release()
    cv2.destroyAllWindows()
    print("[OK] Đã giải phóng camera và đóng cửa sổ.")
    print("[OK] Chương trình kết thúc.")


# ============================================
# ENTRY POINT
# ============================================
if __name__ == "__main__":
    main()
