import { api, connectStateSocket } from "./bridge.js";
import { createRouter } from "./router.js";
import { markDataStale, store } from "./state.js";

const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
const viewRole = new URLSearchParams(location.search).get("view");
const kioskView = viewRole === "kiosk";
const simulatedAdminView = viewRole === "admin";
const debugView = new URLSearchParams(location.search).get("debug") === "1";
document.body.classList.toggle("kiosk-view", kioskView);
document.body.classList.toggle("simulated-admin-view", simulatedAdminView);
if (kioskView) document.title = "FaceCheck - Kiosk quản lý ra vào";
if (simulatedAdminView) $("#enrollmentCameraHeading").textContent = "Theo dõi quét tại kiosk";
$("#testRecognitionImage").hidden = !debugView;
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;
const success = value => String(value || "").includes("Thành công");

function replaceHtml(element, html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  element.replaceChildren(template.content);
}

function toast(message, error = false) {
  const item = document.createElement("div"); item.className = `toast${error ? " error" : ""}`; item.textContent = message;
  $("#toasts").append(item); setTimeout(() => item.remove(), 3200);
}

function updateClock() {
  const now = new Date();
  $("#currentTime").textContent = now.toLocaleTimeString("vi-VN", { hour12: false });
  $("#currentDate").textContent = now.toLocaleDateString("vi-VN", { weekday: "long", day: "2-digit", month: "2-digit", year: "numeric" });
}
updateClock(); setInterval(updateClock, 1000);

const cameraSurface = $("#cameraSurface");
const cameraImage = $("#cameraStream");
const cameraLoadingText = $("#cameraLoadingText");
let cameraStreamActive = false;
let adminLiveViewOpen = false;
cameraImage.addEventListener("load", () => cameraSurface.classList.add("loaded"));

function setCameraStreamActive(active, inactiveMessage = "Đang kết nối camera...") {
  cameraLoadingText.textContent = active ? "Đang kết nối camera..." : inactiveMessage;
  if (active === cameraStreamActive) return;
  cameraStreamActive = active;
  if (active) {
    cameraImage.src = `/api/camera/stream?t=${Date.now()}`;
    return;
  }
  cameraImage.removeAttribute("src");
  cameraSurface.classList.remove("loaded");
}

function syncCameraStream() {
  const mode = store.runtime?.mode;
  const visible = !document.hidden || kioskView;
  const kioskActive = kioskView && ["attendance", "add_employee"].includes(mode);
  const enrollmentActive = !kioskView && store.route === "enrollment" && mode === "add_employee";
  const active = visible && (kioskActive || enrollmentActive || adminLiveViewOpen);
  let message = "Camera trực tiếp được hiển thị tại kiosk";

  if (kioskView) {
    message = mode === "add_employee"
      ? "Đang kết nối camera đăng ký khuôn mặt..."
      : "Đang kết nối camera kiểm soát cửa...";
  }

  setCameraStreamActive(active, message);
}

const fingerprintErrorLabels = {
  mqtt_disconnected: "Thiết bị cửa đang ngoại tuyến.",
  management_command_pending: "Một thao tác vân tay khác đang chạy.",
  invalid_fingerprint_id: "ID vân tay phải nằm trong khoảng 1–127.",
  fingerprint_id_exists: "ID vân tay này đã tồn tại.",
  fingerprint_id_not_found: "Không tìm thấy ID vân tay này.",
  employee_id_exists_select_profile: "Mã nhân viên này đã tồn tại. Hãy chọn nhân viên trong danh sách hồ sơ có sẵn.",
  fingerprint_already_registered: "Ngón tay này đã được đăng ký trước đó. Hãy sử dụng ngón tay khác.",
  publish_error: "Không thể gửi yêu cầu tới thiết bị.",
  publish_failed: "Thiết bị chưa tiếp nhận yêu cầu.",
};
const friendlyFingerprintError = error => fingerprintErrorLabels[error?.message] || error?.message || "Thao tác vân tay thất bại.";

function syncKioskMode(state) {
  if (!kioskView) return;
  const enrollmentMode = state.mode === "add_employee";
  const enrollmentPage = document.querySelector('[data-page="enrollment"]');
  const recognitionPage = document.querySelector('[data-page="recognition"]');
  enrollmentPage.classList.toggle("active", enrollmentMode);
  recognitionPage.classList.toggle("active", !enrollmentMode);
  document.body.classList.toggle("kiosk-enrollment-mode", enrollmentMode);
  moveCamera(enrollmentMode ? "enrollment" : "recognition");
}

function closeCameraFullscreen() {
  cameraSurface.classList.remove("enrollment-fullscreen");
  document.body.classList.remove("camera-fullscreen");
}

$(".camera-expand").addEventListener("click", () => {
  const expanded = cameraSurface.classList.toggle("enrollment-fullscreen");
  document.body.classList.toggle("camera-fullscreen", expanded);
});
addEventListener("keydown", event => { if (event.key === "Escape") closeCameraFullscreen(); });

function moveCamera(route) {
  const slot = document.querySelector(`[data-camera-slot="${route}"]`);
  if (slot && cameraSurface.parentElement !== slot) slot.append(cameraSurface);
}

const enrollmentStatusLabels = [
  [/DA QUET KHUON MAT|SAN SANG LUU/i, "Đã quét khuôn mặt · Sẵn sàng lưu"],
  [/GIU YEN KHUON MAT:\s*([\d.]+)\s*GIAY/i, (_, seconds) => `Giữ yên khuôn mặt · còn ${seconds} giây`],
  [/ROI KHOI KHUNG/i, "Vui lòng rời khung để quét người tiếp theo"],
  [/Di chuyen lai gan hon/i, "Di chuyển lại gần camera hơn"],
  [/Vui long lui lai/i, "Vui lòng lùi ra xa camera một chút"],
  [/Khuon mat chua ro rang/i, "Khuôn mặt chưa rõ ràng"],
  [/Vui long dieu chinh goc ngang cui/i, "Điều chỉnh góc ngẩng hoặc cúi"],
  [/Vui long giu dau thang/i, "Giữ đầu thẳng, không nghiêng"],
  [/Vui long nhin thang vao camera/i, "Vui lòng nhìn thẳng vào camera"],
  [/Goc mat hop le/i, "Góc mặt hợp lệ · Đang chuẩn bị quét"],
  [/Dua mat vao khung hinh/i, "Đưa khuôn mặt vào giữa khung hình"],
];
let enrollmentCancelPending = false;
let enrollmentFormClearedForSuccess = false;
let enrollmentActivityTimer;

function friendlyEnrollmentStatus(value) {
  const status = String(value || "");
  for (const [pattern, replacement] of enrollmentStatusLabels) {
    if (pattern.test(status)) return status.replace(pattern, replacement);
  }
  return status || "Đang chờ khuôn mặt";
}

function setQuality(id, state, detail) {
  const item = $(id);
  item.classList.remove("pass", "warn", "fail", "pending");
  item.classList.add(state);
  item.querySelector("small").textContent = detail;
}

function enrollmentFormIsValid() {
  const form = $("#enrollmentForm");
  const values = Object.fromEntries(new FormData(form));
  const complete = ["name", "id", "role", "department"].every(key => String(values[key] || "").trim());
  const validId = !/[\s<>:"/\\|?*@]/.test(String(values.id || "").trim());
  return complete && validId && form.checkValidity();
}

function updateEnrollmentSteps(enrollment = {}) {
  const formValid = enrollmentFormIsValid();
  const finishing = Boolean(enrollment.saving || enrollment.state === "success");
  const successState = enrollment.state === "success";
  const currentStep = finishing ? 3 : formValid ? 2 : 1;
  const steps = [...document.querySelectorAll("#enrollmentSteps .step")];
  const connectors = [...document.querySelectorAll("#enrollmentSteps > i")];

  steps.forEach((step, index) => {
    const number = index + 1;
    const complete = number < currentStep || (successState && number === 3);
    const active = number === currentStep;
    const accessible = number === 1 || (number === 2 && formValid) || (number === 3 && finishing);
    step.classList.toggle("complete", complete && number !== 3);
    step.classList.toggle("success", successState && number === 3);
    step.classList.toggle("active", active);
    step.setAttribute("aria-disabled", String(!accessible));
    if (active) step.setAttribute("aria-current", "step"); else step.removeAttribute("aria-current");
    step.querySelector("span").innerHTML = complete ? icon("check") : String(number);
  });
  connectors.forEach((connector, index) => connector.classList.toggle("complete", currentStep > index + 1));
  $("#enrollmentSteps").dataset.currentStep = String(currentStep);
}

function updateEnrollmentControls(state) {
  const enrollment = state.enrollment || {};
  const active = state.mode === "add_employee";
  const formValid = enrollmentFormIsValid();
  const startButton = $("#startEnrollmentScan");
  const cancelScanButton = $("#cancelEnrollmentScan");
  const scanArmed = Boolean(enrollment.scan_armed);
  startButton.disabled = !formValid || enrollment.saving || (active && scanArmed);
  startButton.hidden = active && scanArmed;
  cancelScanButton.hidden = !active || !scanArmed || enrollment.capture_ready || enrollment.saving;
  cancelScanButton.disabled = Boolean(enrollment.saving || enrollmentCancelPending);
  startButton.querySelector("span").textContent = active && scanArmed
    ? "Kiosk đang quét khuôn mặt"
    : enrollment.session_active
      ? "Quét người tiếp theo"
      : "Bắt đầu quét tại kiosk";
  $("#saveEnrollment").disabled = !active || !enrollment.capture_ready || enrollment.saving || !formValid;

  const sessionBar = $("#enrollmentSession");
  sessionBar.hidden = !enrollment.session_active;
  const sessionStatus = $("#enrollmentSessionStatus");
  if (enrollment.capture_ready) {
    sessionStatus.textContent = "Đã đủ mẫu · chờ lưu hồ sơ";
  } else {
    sessionStatus.innerHTML = `Tự động kết thúc sau <b id="enrollmentIdleCountdown">${enrollment.idle_remaining_seconds ?? enrollment.idle_timeout_seconds ?? 15}</b> giây`;
  }
  $("#finishEnrollmentSession").disabled = Boolean(enrollment.saving);
}

function renderEnrollment(state) {
  const camera = state.camera || {};
  const enrollment = state.enrollment || {};
  const rawStatus = String(enrollment.status_text || "");
  const normalized = rawStatus.toLowerCase();
  const detected = Number(camera.confidence || 0) > 0;
  const brightness = camera.brightness == null ? null : Number(camera.brightness);
  const ready = Boolean(enrollment.capture_ready);
  const scanning = /giu yen|goc mat hop le/.test(normalized);
  const distanceIssue = /lai gan|lui lai/.test(normalized);
  const poseIssue = /goc|dau thang|nhin thang|ngang cui/.test(normalized) && !scanning;
  const clarityIssue = /chua ro/.test(normalized);
  const offline = !camera.running;
  const sampleCount = Number(enrollment.sample_count || 0);
  const sampleTarget = Number(enrollment.sample_target || 5);
  const friendlyStatus = sampleCount > 0 && !ready
    ? `${friendlyEnrollmentStatus(rawStatus)} · ${sampleCount}/${sampleTarget} mẫu`
    : friendlyEnrollmentStatus(rawStatus);

  $("#enrollmentFps").textContent = Math.round(camera.fps || 0);
  $("#saveEnrollment span").textContent = enrollment.saving ? "Đang lưu khuôn mặt..." : "Lưu khuôn mặt";
  const cancelCapture = $("#cancelEnrollmentCapture");
  cancelCapture.hidden = !ready || enrollment.saving;
  cancelCapture.disabled = Boolean(enrollment.saving || enrollmentCancelPending);
  let visualState = "idle";
  let title = "Đưa khuôn mặt vào khung";
  let message = friendlyStatus;
  let iconName = "user-scan";
  if (offline) { visualState = "error"; title = "Camera chưa sẵn sàng"; message = "Kiểm tra kết nối camera để tiếp tục"; iconName = "x"; }
  else if (enrollment.state === "error") { visualState = "error"; title = enrollment.title; message = enrollment.message; iconName = "x"; }
  else if (enrollment.saving) { visualState = "scanning"; title = "Đang lưu khuôn mặt"; message = enrollment.message || "Đang trích xuất dữ liệu khuôn mặt..."; iconName = "save"; }
  else if (ready) { visualState = "ready"; title = "Khuôn mặt đạt yêu cầu"; message = "Sẵn sàng lưu hồ sơ nhân viên"; iconName = "check"; }
  else if (enrollment.state === "success") { visualState = "success"; title = enrollment.title; message = enrollment.message; iconName = "check"; }
  else if (scanning) { visualState = "scanning"; title = "Đang quét khuôn mặt"; iconName = "user-scan"; }
  else if (detected) { visualState = "warning"; title = "Cần căn chỉnh khuôn mặt"; iconName = "alert"; }

  const result = $("#enrollmentResult");
  result.dataset.state = visualState;
  result.querySelector(".enrollment-result-icon use").setAttribute("href", `#i-${iconName}`);
  $("#enrollmentResultTitle").textContent = title;
  $("#enrollmentCameraStatus").textContent = message;
  updateEnrollmentSteps(enrollment);

  setQuality(
    "#qualityLight",
    offline ? "fail" : !detected || brightness == null ? "pending" : camera.light_ok ? "pass" : "fail",
    offline ? "Chưa kết nối" : !detected || brightness == null ? "Chờ khuôn mặt" : camera.light_ok ? `Độ sáng ${Math.round(brightness)}%` : `Thiếu sáng ${Math.round(brightness)}%`,
  );
  setQuality("#qualityPosition", ready || scanning ? "pass" : distanceIssue || poseIssue ? "warn" : "pending", ready ? "Đã căn chuẩn" : scanning ? "Giữ nguyên vị trí" : distanceIssue || poseIssue ? "Cần điều chỉnh" : "Chờ khuôn mặt");
  setQuality("#qualityClarity", ready || scanning ? "pass" : clarityIssue ? "fail" : detected ? "warn" : "pending", ready ? "Đạt yêu cầu" : clarityIssue ? "Chưa rõ ràng" : detected ? `Tin cậy ${Math.round((camera.confidence || 0) * 100)}%` : "Đang kiểm tra");

  const guidance = offline
    ? "Camera đang ngoại tuyến. Hãy kiểm tra thiết bị trước khi quét."
    : ready
      ? "Ảnh đã được khóa sau khi giữ ổn định 3 giây và thu đủ mẫu. Hãy kiểm tra thông tin rồi lưu."
      : "Hệ thống tự kiểm tra mắt, mũi, miệng, vị trí, khoảng cách và góc nhìn; giữ yên 3 giây khi khung đạt chuẩn.";
  $("#enrollmentNotice").textContent = guidance;
}

function renderStatus(state) {
  store.runtime = state;
  syncKioskMode(state);
  syncCameraStream();
  markDataStale(state.history_version);
  $("#sideCamera").textContent = state.camera.running ? "Hoạt động" : "Ngoại tuyến";
  $("#sideDevice").textContent = state.camera.device === "cpu" ? "CPU đang xử lý" : "GPU đang xử lý";
  const door = state.door || {};
  const deviceStatus = door.device_status || {};
  const lastSeenAt = Date.parse(deviceStatus.received_at || "");
  const heartbeatFresh = Number.isFinite(lastSeenAt) && Date.now() - lastSeenAt < 90000;
  const controllerOnline = deviceStatus.state === "online" && heartbeatFresh;
  $("#sideDoor").textContent = controllerOnline ? "Trực tuyến" : "Ngoại tuyến";
  const monitorCamera = $("#monitorCameraState");
  if (monitorCamera) {
    monitorCamera.textContent = state.camera.running ? "Đang hoạt động" : "Ngoại tuyến";
    $("#monitorRecognitionState").textContent = ["recognizing", "strict_confirmation"].includes(state.recognition.state) ? "Đang xác thực" : "Sẵn sàng";
    $("#monitorFaceCount").textContent = `${Number(state.camera.faces || 0)} khuôn mặt`;
    $("#monitorProcessingFps").textContent = `${Math.round(state.camera.fps || 0)} FPS`;
    $("#monitorDoorState").textContent = controllerOnline ? "Đang trực tuyến" : "Ngoại tuyến / chưa xác nhận";
    $("#monitorFingerprintState").textContent = door.fingerprint_sensor_state === "sensor_online"
      ? "Đang hoạt động"
      : door.fingerprint_sensor_state === "sensor_offline"
        ? "Mất kết nối"
        : "Chưa có trạng thái";
  }
  if (!kioskView && state.enrollment?.state === "success") {
    if (!enrollmentFormClearedForSuccess) {
      clearFaceEnrollmentForm();
      enrollmentFormClearedForSuccess = true;
      store.employees.stale = true;
      store.dashboard.stale = true;
    }
  } else if (state.enrollment?.state !== "success") {
    enrollmentFormClearedForSuccess = false;
  }
  renderEnrollment(state);
  updateEnrollmentControls(state);
  const access = state.access || state.recognition;
  const accessSignature = JSON.stringify(access);
  if (store.accessSignature !== accessSignature) {
    store.accessSignature = accessSignature;
    renderRecognition(access);
  }
  renderRecognitionTest(state.recognition_test);
  if (store.route === "recognition" && store.history.stale) loadRecent();
  // Fingerprint commands can be started from the employee page. Process their
  // ACKs on every admin route so the employee row refreshes as soon as the
  // controller confirms enrollment or deletion.
  if (!kioskView) renderFingerprintState(state.door || {});
}

function renderRecognitionTest(test) {
  if (!test) return;
  const button = $("#testRecognitionImage");
  button.disabled = test.state === "processing";
  button.textContent = test.state === "processing" ? "Đang phân tích ảnh..." : "Công cụ kiểm thử ảnh";
  if (test.version <= store.recognitionTestVersion || !["complete", "error"].includes(test.state)) return;
  store.recognitionTestVersion = test.version;
  if (test.error) { toast(test.error, true); return; }
  const result = test.result;
  openModal(`<h2>Kết quả kiểm thử ảnh</h2><p class="subtitle">${esc(result.file)} · Không ghi vào nhật ký xác thực</p><div class="test-result"><strong>${esc(result.conclusion)}</strong><p>Top 1: ${esc(result.best.name || "Không rõ")} (${esc(result.best.id || "---")}) · Distance ${esc(result.best_distance)}</p><p>Top 2: ${esc(result.second.name || "Không có")} (${esc(result.second.id || "---")}) · Distance ${esc(result.second_distance ?? "---")}</p><p>Margin: ${esc(result.margin)}</p></div><div class="modal-actions"><button class="primary" id="closeTestResult">Đóng</button></div>`);
  $("#closeTestResult").addEventListener("click", closeModal);
}

function renderRecognition(result) {
  const panel = $("#recognitionResult"); panel.dataset.state = result.state;
  const iconName = ["success", "duplicate"].includes(result.state) ? "check" : result.state === "ambiguous" ? "alert" : result.state === "unknown" ? "x" : "target";
  panel.querySelector(".result-icon use").setAttribute("href", `#i-${iconName}`);
  panel.querySelector(".result-header h2").textContent = result.title;
  panel.querySelector(".result-header p").textContent = result.message;
  const employee = result.employee;
  const avatar = employee?.image_url
    ? `<img class="avatar" src="${esc(employee.image_url)}" alt="${esc(employee.name)}">`
    : `<div class="avatar placeholder">${icon(result.method === "fingerprint" ? "fingerprint" : "users")}</div>`;
  panel.querySelector(".employee-summary").innerHTML = employee ? `${avatar}<div><h3>${esc(employee.name)}</h3><p>Mã NV: <b>${esc(employee.id || "—")}</b></p><p>Chức vụ: ${esc(employee.role || "—")}</p><p>Phòng ban: ${esc(employee.department || "—")}</p></div>` : `${avatar}<div><h3>${result.state === "unknown" ? "Chưa xác định" : result.state === "ambiguous" ? "Chưa xác định" : "Chưa có lượt xác thực"}</h3><p>Mã NV: <b>---</b></p><p>Chức vụ: ---</p><p>Phòng ban: ---</p></div>`;
  const notice = panel.querySelector(".result-notice");
  notice.querySelector("strong").textContent = result.access_label || "Sẵn sàng tiếp nhận xác thực";
  notice.querySelector("span").textContent = `Phương thức: ${result.method_label || "Khuôn mặt hoặc vân tay"}`;
  panel.querySelector(".result-time b").textContent = result.timestamp || "Chờ xác thực...";
  const retry = $("#retryRecognition");
  retry.hidden = result.method === "fingerprint" || !["ambiguous", "unknown", "error"].includes(result.state);
  const accessStatus = panel.querySelector(".status-action");
  accessStatus.hidden = false;
  accessStatus.className = `status-action ${result.access_state || "waiting"}`;
  accessStatus.textContent = `Quyền ra vào: ${result.access_label || "Sẵn sàng xác thực"}`;
}

function rowsHtml(rows) {
  if (!rows.length) return `<tr><td class="empty" colspan="6">Chưa có dữ liệu</td></tr>`;
  return rows.map(row => {
    const allowed = success(row.status);
    return `<tr><td>${esc(row.time)}</td><td><strong>${esc(row.name)}</strong></td><td>${esc(row.id)}</td><td>${esc(row.role)}</td><td>${esc(row.dept)}</td><td><span class="status-text ${allowed ? "success" : "failed"}">${allowed ? "Xác thực thành công" : "Xác thực bị từ chối"}</span></td></tr>`;
  }).join("");
}

async function loadRecent() {
  try { const data = await api.history({ page: 1, pageSize: 5 }); replaceHtml($("#recentAttendance"), rowsHtml(data.items)); } catch (error) { toast(error.message, true); }
}

function statCard(title, value, iconName) { return `<article class="card stat-card"><span class="stat-icon">${icon(iconName)}</span><span><small>${esc(title)}</small><strong>${esc(value)}</strong></span></article>`; }

function dashboardMetric({ tone, iconName, label, value, note, decoration = "bars" }) {
  return `<article class="card dashboard-metric ${tone}"><span class="metric-icon">${icon(iconName)}</span><div class="metric-copy"><small>${esc(label)}</small><strong>${esc(value)}</strong><span>${esc(note)}</span></div><div class="metric-decoration ${decoration}" aria-hidden="true"><i></i><i></i><i></i><i></i></div></article>`;
}

function parseAttendanceTime(value) {
  const match = String(value || "").match(/(\d{2}):(\d{2}):(\d{2})\s+(\d{2})\/(\d{2})\/(\d{4})/);
  if (!match) return null;
  return new Date(Number(match[6]), Number(match[5]) - 1, Number(match[4]), Number(match[1]), Number(match[2]), Number(match[3]));
}

function relativeTime(value) {
  const date = parseAttendanceTime(value);
  if (!date) return value || "---";
  const minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return "Vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  return `${Math.floor(hours / 24)} ngày trước`;
}

async function loadDashboard(force = false) {
  if (!force && !store.dashboard.stale && store.dashboard.data) return;
  try {
    const data = await api.dashboard(); store.dashboard.data = data; store.dashboard.stale = false;
    const online = store.runtime?.camera?.running;
    replaceHtml($("#dashboardStats"), [
      dashboardMetric({ tone: "green", iconName: "monitor", label: "Kiosk xác thực", value: online ? "Hoạt động" : "Ngoại tuyến", note: online ? "Camera đang kết nối" : "Camera chưa kết nối", decoration: "pulse" }),
      dashboardMetric({ tone: "blue", iconName: "users", label: "Nhân viên", value: `${data.employees} hồ sơ`, note: "Hồ sơ đang có trong hệ thống" }),
      dashboardMetric({ tone: "orange", iconName: "clock", label: "Lượt xác thực", value: `${data.today_total} lượt`, note: "Trong ngày hôm nay" }),
      dashboardMetric({ tone: "violet", iconName: "target", label: "Tỷ lệ thành công", value: `${data.success_rate}%`, note: "Trong ngày hôm nay", decoration: "wave" }),
    ].join(""));
    const max = Math.max(1, ...data.week.map(day => day.value));
    const currentWeekday = (new Date().getDay() + 6) % 7;
    replaceHtml($("#weekChart"), `<div class="chart-grid"><span>50</span><span>40</span><span>30</span><span>20</span><span>10</span><span>0</span></div><div class="chart-bars">${data.week.map((day, index) => `<div class="chart-column"><div class="chart-value">${day.value}</div><div class="chart-bar-track"><i class="chart-bar-fill${index === currentWeekday ? " current" : ""}" style="height:${Math.max(3, day.value / max * 100)}%"></i></div><strong>${esc(day.label)}</strong><span>${esc(day.date)}</span></div>`).join("")}</div>`);
    replaceHtml($("#dashboardRecent"), data.recent.length ? data.recent.map(row => {
      const ok = success(row.status);
      return `<div class="timeline-item ${ok ? "ok" : "fail"}"><span class="timeline-dot"></span><span class="timeline-avatar">${icon(ok ? "check" : "x")}</span><div class="timeline-copy"><strong>${esc(row.name)}</strong><span>${ok ? "Danh tính đã được xác thực" : esc(row.reason || "Xác thực bị từ chối")}</span></div><time>${esc(relativeTime(row.time))}</time><span class="timeline-status">${ok ? "Thành công" : "Từ chối"}</span></div>`;
    }).join("") : `<div class="empty">Chưa có hoạt động</div>`);
    const failureRate = Math.max(0, 100 - data.success_rate);
    replaceHtml($("#successOverview"), `<div class="success-donut" style="--rate:${data.success_rate}"><div><strong>${data.success_rate}%</strong><span>Thành công</span></div></div><div class="success-legend"><div><i class="legend-blue"></i><span>Được phép mở cửa</span><strong>${data.success_rate}%<small>${data.today_success} lượt</small></strong></div><div><i class="legend-red"></i><span>Bị từ chối</span><strong>${failureRate}%<small>${data.today_failed} lượt</small></strong></div></div>`);
  } catch (error) { toast(error.message, true); }
}

function pagination(element, data, onPage, itemLabel = "bản ghi") {
  const buttons = [`<span class="page-summary">${data.total} ${itemLabel} · ${data.page_size}/trang · trang ${data.page}/${data.pages}</span>`, `<button ${data.page <= 1 ? "disabled" : ""} data-p="${data.page - 1}">‹</button>`];
  const visiblePages = new Set([1, data.pages]);
  for (let page = Math.max(1, data.page - 1); page <= Math.min(data.pages, data.page + 1); page++) visiblePages.add(page);
  let previousPage = 0;
  [...visiblePages].sort((a, b) => a - b).forEach(page => {
    if (page - previousPage > 1) buttons.push(`<span class="page-ellipsis">…</span>`);
    buttons.push(`<button class="${page === data.page ? "active" : ""}" data-p="${page}">${page}</button>`);
    previousPage = page;
  });
  buttons.push(`<button ${data.page >= data.pages ? "disabled" : ""} data-p="${data.page + 1}">›</button>`);
  element.innerHTML = buttons.join(""); element.querySelectorAll("button:not([disabled])").forEach(button => button.addEventListener("click", () => onPage(Number(button.dataset.p))));
}

let employeeRequestVersion = 0;
async function loadEmployees(force = false) {
  const state = store.employees; if (!force && !state.stale && state.data) return;
  const requestVersion = ++employeeRequestVersion;
  try {
    const data = await api.employees({ query: state.query, page: state.page, pageSize: state.pageSize });
    if (requestVersion !== employeeRequestVersion) return;
    state.data = data; state.page = data.page; state.stale = false;
    $("#employeeStats").innerHTML = statCard("Tổng hồ sơ", data.all_total, "users") + statCard("Có khuôn mặt", data.face_total, "user-scan") + statCard("Có vân tay", data.fingerprint_total, "fingerprint") + statCard("Có cả hai", data.both_total, "check");
    replaceHtml($("#employeeRows"), data.items.length ? data.items.map(item => {
      const avatar = item.image_url
        ? `<img class="row-avatar" loading="lazy" src="${esc(item.image_url)}" alt="">`
        : `<span class="row-avatar placeholder">${icon("users")}</span>`;
      const fingerprintLabel = item.fingerprint_registered
        ? item.fingerprint_ids.map(id => `#${id}`).join(", ")
        : "Chưa đăng ký";
      const faceStatus = item.face_registered
        ? `<span class="method-status active">${icon("user-scan")} ${item.recognition_enabled ? "Đã đăng ký" : "Đang tạm khóa"}</span>`
        : `<span class="method-status missing">Chưa đăng ký</span>`;
      const fingerprintStatus = item.fingerprint_registered
        ? `<span class="method-status active">${icon("fingerprint")} ${esc(fingerprintLabel)}</span>`
        : `<span class="method-status missing">Chưa đăng ký</span>`;
      const fingerprintAction = item.fingerprint_registered
        ? `<button class="icon-button" data-action="manage-fingerprint" data-id="${esc(item.id)}" aria-label="Quản lý vân tay">${icon("fingerprint")}</button>`
        : `<button class="icon-button" data-action="add-fingerprint" data-id="${esc(item.id)}" aria-label="Thêm vân tay">${icon("fingerprint")}</button>`;
      const faceActions = item.face_registered
        ? `<button class="icon-button" data-action="edit" data-id="${esc(item.id)}" aria-label="Sửa hồ sơ">${icon("edit")}</button><button class="icon-button" data-action="toggle" data-id="${esc(item.id)}" aria-label="Bật hoặc khóa khuôn mặt">${icon("target")}</button>`
        : `<button class="icon-button" data-action="edit" data-id="${esc(item.id)}" aria-label="Sửa hồ sơ">${icon("edit")}</button><button class="icon-button" data-action="add-face" data-id="${esc(item.id)}" aria-label="Thêm khuôn mặt">${icon("user-scan")}</button>`;
      const deleteAction = item.face_registered || item.fingerprint_registered
        ? `<button class="icon-button danger" data-action="delete-auth" data-id="${esc(item.id)}" aria-label="Xóa phương thức xác thực">${icon("trash")}</button>`
        : "";
      return `<tr><td><div class="employee-identity">${avatar}<span><strong>${esc(item.name)}</strong><small>${esc(item.id)}</small></span></div></td><td>${esc(item.role)}</td><td>${esc(item.department)}</td><td>${faceStatus}</td><td>${fingerprintStatus}</td><td><span class="status-text ${item.access_enabled ? "success" : "failed"}">${item.access_enabled ? "Được phép" : "Tạm khóa"}</span></td><td><div class="actions"><button class="icon-button" data-action="profile" data-id="${esc(item.id)}" aria-label="Xem hồ sơ">${icon("eye")}</button>${fingerprintAction}${faceActions}${deleteAction}</div></td></tr>`;
    }).join("") : `<tr><td class="empty" colspan="7">Không tìm thấy nhân viên</td></tr>`);
    pagination($("#employeePagination"), data, page => { state.page = page; state.stale = true; loadEmployees(); }, "nhân viên");
  } catch (error) { toast(error.message, true); }
}

async function loadHistory(force = false) {
  const state = store.history; if (!force && !state.stale && state.data) return;
  try { const data = await api.history({ query: state.query, status: state.status, page: state.page, pageSize: state.pageSize }); state.data = data; state.page = data.page; state.stale = false; replaceHtml($("#historyRows"), rowsHtml(data.items)); pagination($("#historyPagination"), data, page => { state.page = page; state.stale = true; loadHistory(); }, "bản ghi"); } catch (error) { toast(error.message, true); }
}

let lastFingerprintManagementSignature = "";
let pendingFingerprintEnrollmentId = null;
let fingerprintEmployeeDirectory = [];
let selectedFingerprintEmployeeId = "";
let selectedFaceEmployeeId = "";

function populateEmployeeSelectors() {
  const label = employee => `${employee.name} · ${employee.id} · ${employee.department}`;
  const faceCandidates = fingerprintEmployeeDirectory.filter(employee => !employee.face_registered);
  replaceHtml($("#faceEmployeeSelect"), `<option value="">Nhân viên mới — nhập thông tin bên dưới</option>${faceCandidates.map(employee => `<option value="${esc(employee.id)}">${esc(label(employee))}</option>`).join("")}`);
  replaceHtml($("#fingerprintEmployeeSelect"), `<option value="">Nhân viên mới — nhập thông tin bên dưới</option>${fingerprintEmployeeDirectory.map(employee => `<option value="${esc(employee.id)}">${esc(label(employee))}</option>`).join("")}`);
}

async function loadEnrollmentEmployeeDirectory() {
  const employees = await api.employees({ page: 1, pageSize: 100 });
  fingerprintEmployeeDirectory = employees.items || [];
  populateEmployeeSelectors();
}

function syncFaceEmployeeFields() {
  const form = $("#enrollmentForm");
  const employeeId = form.elements.namedItem("existing_employee_id").value;
  const employee = fingerprintEmployeeDirectory.find(item => item.id.toLocaleLowerCase() === employeeId.toLocaleLowerCase());
  const profileFields = ["name", "id", "role", "department"];
  if (employee) {
    fillForm(form, { name: employee.name, id: employee.id, role: employee.role, department: employee.department });
    profileFields.forEach(name => { form.elements.namedItem(name).readOnly = true; });
    selectedFaceEmployeeId = employee.id;
    return;
  }
  profileFields.forEach(name => { form.elements.namedItem(name).readOnly = false; });
  if (selectedFaceEmployeeId) {
    profileFields.forEach(name => { form.elements.namedItem(name).value = ""; });
    selectedFaceEmployeeId = "";
  }
}

function clearFaceEnrollmentForm() {
  const form = $("#enrollmentForm");
  form.reset();
  ["name", "id", "role", "department"].forEach(name => {
    form.elements.namedItem(name).readOnly = false;
  });
  selectedFaceEmployeeId = "";
}

function nextAvailableFingerprintId() {
  const used = new Set(
    (store.fingerprints.data?.items || []).map(item => Number(item.fingerprint_id)),
  );
  return Array.from({ length: 127 }, (_, index) => index + 1)
    .find(id => !used.has(id)) || "";
}

function fillNextFingerprintId({ overwrite = false } = {}) {
  const field = $("#fingerprintEnrollForm").elements.namedItem("fingerprint_id");
  if (overwrite || !field.value) field.value = nextAvailableFingerprintId();
}

function syncFingerprintEmployeeFields() {
  const form = $("#fingerprintEnrollForm");
  const hint = $("#fingerprintProfileHint");
  const submitButton = $("#fingerprintEnrollButton");
  const selectedId = form.elements.namedItem("existing_employee_id").value;
  const employee = fingerprintEmployeeDirectory.find(item => item.id.toLocaleLowerCase() === selectedId.toLocaleLowerCase());
  const profileFields = ["name", "role", "department"];
  if (employee) {
    fillForm(form, { employee_id: employee.id, name: employee.name, role: employee.role, department: employee.department });
    form.elements.namedItem("existing_employee_id").value = employee.id;
    form.elements.namedItem("employee_id").readOnly = true;
    profileFields.forEach(name => { form.elements.namedItem(name).readOnly = true; });
    selectedFingerprintEmployeeId = employee.id;
    const registeredIds = employee.fingerprint_ids || [];
    hint.textContent = registeredIds.length
      ? `${employee.name} đã có vân tay ID ${registeredIds.map(id => `#${id}`).join(", ")}. Chỉ đăng ký thêm bằng một ngón tay khác.`
      : `${employee.name} chưa có vân tay. Hệ thống sẽ liên kết mẫu mới với hồ sơ này.`;
    submitButton.textContent = registeredIds.length ? "Đăng ký thêm vân tay" : "Bắt đầu đăng ký";
    fillNextFingerprintId();
    return;
  }
  hint.textContent = "Chọn nhân viên hiện có hoặc nhập đầy đủ thông tin để tạo một hồ sơ mới.";
  submitButton.textContent = "Bắt đầu đăng ký";
  form.elements.namedItem("employee_id").readOnly = false;
  profileFields.forEach(name => { form.elements.namedItem(name).readOnly = false; });
  if (selectedFingerprintEmployeeId) {
    form.elements.namedItem("employee_id").value = "";
    profileFields.forEach(name => { form.elements.namedItem(name).value = ""; });
    selectedFingerprintEmployeeId = "";
  }
}

function clearFingerprintEnrollForm() {
  const form = $("#fingerprintEnrollForm");
  form.reset();
  form.querySelectorAll("input").forEach(input => {
    input.value = "";
    input.defaultValue = "";
    input.readOnly = false;
  });
  selectedFingerprintEmployeeId = "";
  $("#fingerprintProfileHint").textContent = "Chọn nhân viên hiện có hoặc nhập đầy đủ thông tin để tạo một hồ sơ mới.";
  $("#fingerprintEnrollButton").textContent = "Bắt đầu đăng ký";
}

function renderFingerprintState(door = {}) {
  const management = door.fingerprint_management || {};
  const state = management.state || "idle";
  const commandLabel = management.command === "FINGERPRINT_ENROLL" ? "Đăng ký" : management.command === "FINGERPRINT_DELETE" ? "Xóa" : "Không có";
  const stateLabels = { sent: "Đang gửi yêu cầu", started: "Đang thực hiện", finished: "Hoàn tất", failed: "Thất bại", idle: "Sẵn sàng" };
  const sensorLabels = { sensor_online: "Đang hoạt động", sensor_offline: "Mất kết nối", unknown: "Chưa có trạng thái" };
  const css = ["sent", "started"].includes(state) ? "fingerprint-command-busy" : state === "finished" ? "fingerprint-command-ok" : state === "failed" ? "fingerprint-command-failed" : "";
  replaceHtml($("#fingerprintManagementState"), `
    <div><dt>Kết nối thiết bị</dt><dd>${door.connected ? "Đã kết nối" : "Mất kết nối"}</dd></div>
    <div><dt>Cảm biến vân tay</dt><dd>${esc(sensorLabels[door.fingerprint_sensor_state] || sensorLabels.unknown)}</dd></div>
    <div><dt>Vân tay đang quản lý</dt><dd>${Number(door.fingerprint_count || 0)}</dd></div>
    <div><dt>Thao tác gần nhất</dt><dd>${esc(commandLabel)}${management.fingerprint_id ? ` vân tay #${esc(management.fingerprint_id)}` : ""}</dd></div>
    <div><dt>Tiến trình</dt><dd class="${css}">${esc(stateLabels[state] || state)}${management.reason ? ` · ${esc(fingerprintErrorLabels[management.reason] || "Cần kiểm tra lại")}` : ""}</dd></div>`);
  const busy = ["sent", "started"].includes(state);
  $("#fingerprintEnrollButton").disabled = busy || !door.connected;
  $("#cancelFingerprintEnrollment").disabled = busy;
  const signature = `${management.command_id || ""}:${state}`;
  if (signature !== lastFingerprintManagementSignature) {
    lastFingerprintManagementSignature = signature;
    if (["finished", "failed"].includes(state)) {
      if (state === "finished" && management.command === "FINGERPRINT_ENROLL" && Number(management.fingerprint_id) === pendingFingerprintEnrollmentId) {
        clearFingerprintEnrollForm();
        pendingFingerprintEnrollmentId = null;
      }
      store.fingerprints.stale = true;
      store.employees.stale = true;
      store.dashboard.stale = true;
      loadFingerprints(true);
      if (store.route === "employees") loadEmployees(true);
      toast(state === "finished" ? "Thiết bị đã hoàn tất thao tác vân tay." : `Thao tác vân tay thất bại: ${fingerprintErrorLabels[management.reason] || "cần kiểm tra lại"}`, state === "failed");
    }
  }
}

function renderFingerprintDirectory() {
  const state = store.fingerprints;
  const items = state.data?.items || [];
  const pageSize = state.pageSize || 10;
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  state.page = Math.min(Math.max(1, state.page || 1), pages);
  const start = (state.page - 1) * pageSize;
  const visibleItems = items.slice(start, start + pageSize);
  replaceHtml($("#fingerprintRows"), visibleItems.length ? visibleItems.map(item => {
    const employee = fingerprintEmployeeDirectory.find(candidate =>
      candidate.id.toLocaleLowerCase() === String(item.employee_id || "").toLocaleLowerCase()
    );
    const name = item.name || employee?.name || `Vân tay #${item.fingerprint_id}`;
    const employeeId = item.employee_id || employee?.id || "—";
    const role = item.role || employee?.role || "—";
    const department = item.department || employee?.department || "—";
    return `<tr><td><span class="method-status active">${icon("fingerprint")} #${esc(item.fingerprint_id)}</span></td><td><strong>${esc(name)}</strong></td><td>${esc(employeeId)}</td><td>${esc(role)}</td><td>${esc(department)}</td><td><span class="status-text success">Đã đăng ký</span></td></tr>`;
  }).join("") : `<tr><td class="empty" colspan="6">Chưa có nhân viên nào đăng ký vân tay</td></tr>`);
  pagination($("#fingerprintPagination"), {
    total: items.length,
    page: state.page,
    page_size: pageSize,
    pages,
  }, page => {
    state.page = page;
    renderFingerprintDirectory();
  }, "vân tay");
}

async function loadFingerprints(force = false) {
  const state = store.fingerprints;
  if (!force && !state.stale && state.data) return;
  try {
    const [data, employees] = await Promise.all([
      api.fingerprints(),
      api.employees({ page: 1, pageSize: 100 }),
    ]);
    fingerprintEmployeeDirectory = employees.items || [];
    populateEmployeeSelectors();
    replaceHtml($("#fingerprintEmployeeOptions"), fingerprintEmployeeDirectory.map(employee =>
      `<option value="${esc(employee.id)}">${esc(employee.name)} · ${esc(employee.role)} · ${esc(employee.department)}</option>`
    ).join(""));
    state.data = data;
    state.stale = false;
    renderFingerprintDirectory();
    renderFingerprintState(store.runtime?.door || {});
  } catch (error) { toast(friendlyFingerprintError(error), true); }
}

const modal = $("#modal");
let focusBeforeModal = null;
function closeModal() {
  if (adminLiveViewOpen) {
    adminLiveViewOpen = false;
    moveCamera(store.route === "enrollment" ? "enrollment" : "recognition");
    syncCameraStream();
  }
  modal.hidden = true;
  $("#modalContent").innerHTML = "";
  focusBeforeModal?.focus?.();
  focusBeforeModal = null;
}
function openModal(html) {
  focusBeforeModal = document.activeElement;
  $("#modalContent").innerHTML = html;
  modal.hidden = false;
  modal.querySelector("input,button")?.focus();
}
modal.querySelector(".modal-close").addEventListener("click", closeModal); modal.addEventListener("click", event => { if (event.target === modal) closeModal(); }); addEventListener("keydown", event => { if (event.key === "Escape" && !modal.hidden) closeModal(); });
modal.addEventListener("keydown", event => {
  if (event.key !== "Tab") return;
  const focusable = [...modal.querySelectorAll("button,input,select,textarea,[href],[tabindex]:not([tabindex='-1'])")].filter(item => !item.disabled && !item.hidden);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});

function fillForm(form, values) {
  Object.entries(values).forEach(([name, value]) => {
    const field = form.elements.namedItem(name);
    if (field) field.value = value || "";
  });
  form.dispatchEvent(new Event("input", { bubbles: true }));
}

async function openFaceEnrollment(employee = null) {
  closeModal();
  await navigate("enrollment");
  const form = $("#enrollmentForm");
  clearFaceEnrollmentForm();
  if (employee) {
    form.elements.namedItem("existing_employee_id").value = employee.id;
    syncFaceEmployeeFields();
  }
  form.elements.namedItem(employee ? "existing_employee_id" : "name")?.focus();
}

async function openFingerprintEnrollment(employee = null) {
  closeModal();
  await navigate("fingerprints");
  const form = $("#fingerprintEnrollForm");
  fillForm(form, {
    existing_employee_id: employee?.id || "",
    employee_id: employee?.id || "",
    name: employee?.name || "",
    role: employee?.role || "",
    department: employee?.department || "",
  });
  syncFingerprintEmployeeFields();
  fillNextFingerprintId({ overwrite: true });
  form.elements.namedItem("fingerprint_id")?.focus();
}

function openFingerprintDelete(employee, fingerprintId) {
  openModal(`<h2>Xóa vân tay ID #${fingerprintId}?</h2><p class="subtitle">Mẫu vân tay của <strong>${esc(employee.name)}</strong> sẽ bị xóa khỏi thiết bị và không thể khôi phục. Hồ sơ nhân viên và khuôn mặt vẫn được giữ nguyên.</p><form id="fingerprintDeleteForm"><label>Xác nhận ID<input id="fingerprintDeleteConfirmation" inputmode="numeric" autocomplete="off" placeholder="Nhập ${fingerprintId}" required></label><div class="modal-actions"><button type="button" class="secondary" id="cancelFingerprintDelete">Hủy</button><button class="primary" id="confirmFingerprintDelete" disabled>Xóa vân tay</button></div></form>`);
  const confirmation = $("#fingerprintDeleteConfirmation");
  const confirmButton = $("#confirmFingerprintDelete");
  confirmation.addEventListener("input", () => {
    confirmButton.disabled = confirmation.value.trim() !== String(fingerprintId);
  });
  $("#cancelFingerprintDelete").addEventListener("click", closeModal);
  $("#fingerprintDeleteForm").addEventListener("submit", async event => {
    event.preventDefault();
    if (confirmation.value.trim() !== String(fingerprintId)) return;
    confirmButton.disabled = true;
    try {
      await api.deleteFingerprint(fingerprintId, confirmation.value.trim());
      closeModal();
      store.fingerprints.stale = true;
      store.employees.stale = true;
      toast(`Đã gửi yêu cầu xóa vân tay ID #${fingerprintId}.`);
    } catch (error) {
      confirmButton.disabled = false;
      toast(friendlyFingerprintError(error), true);
    }
  });
}

function openFingerprintManager(employee) {
  const ids = employee.fingerprint_ids || [];
  if (!ids.length) {
    openFingerprintEnrollment(employee).catch(error => toast(error.message, true));
    return;
  }
  const items = ids.map(id => `<div class="fingerprint-manager-item"><span><strong>Vân tay ID #${esc(id)}</strong><small>Đang liên kết với ${esc(employee.id)} · ${esc(employee.name)}</small></span><button class="icon-button danger" type="button" data-remove-fingerprint="${esc(id)}" aria-label="Xóa vân tay ID ${esc(id)}">${icon("trash")}</button></div>`).join("");
  openModal(`<h2>Quản lý vân tay</h2><p class="subtitle">Các mẫu vân tay của <strong>${esc(employee.name)}</strong>. Thông tin cá nhân được chỉnh sửa bằng nút sửa hồ sơ tại danh sách nhân viên.</p><div class="fingerprint-manager-list">${items}</div><div class="modal-actions"><button class="secondary" id="closeFingerprintManager" type="button">Đóng</button><button class="primary" id="addAnotherFingerprint" type="button">+ Đăng ký thêm</button></div>`);
  $("#closeFingerprintManager").addEventListener("click", closeModal);
  $("#addAnotherFingerprint").addEventListener("click", () => openFingerprintEnrollment(employee).catch(error => toast(error.message, true)));
  $("#modalContent").querySelectorAll("[data-remove-fingerprint]").forEach(button => {
    button.addEventListener("click", () => openFingerprintDelete(employee, Number(button.dataset.removeFingerprint)));
  });
}

function openFaceDelete(employee) {
  openModal(`<h2>Xóa khuôn mặt?</h2><p class="subtitle">Dữ liệu khuôn mặt của <strong>${esc(employee.name)}</strong> sẽ bị xóa.${employee.fingerprint_registered ? " Vân tay vẫn được giữ và nhân viên vẫn có thể ra vào bằng vân tay." : " Hồ sơ sẽ không còn phương thức xác thực nào."}</p><div class="modal-actions"><button class="secondary" id="cancelDelete">Hủy</button><button class="primary" id="confirmDelete">Xóa khuôn mặt</button></div>`);
  $("#cancelDelete").addEventListener("click", closeModal);
  $("#confirmDelete").addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      await api.deleteEmployee(employee.id);
      closeModal();
      store.employees.stale = true;
      store.dashboard.stale = true;
      await loadEmployees();
      toast("Đã xóa dữ liệu khuôn mặt");
    } catch (error) {
      event.currentTarget.disabled = false;
      toast(error.message, true);
    }
  });
}

function openFingerprintDeleteChoice(employee) {
  const ids = employee.fingerprint_ids || [];
  if (ids.length === 1) {
    openFingerprintDelete(employee, Number(ids[0]));
    return;
  }
  openFingerprintManager(employee);
}

function openAuthenticationDelete(employee) {
  if (employee.face_registered && employee.fingerprint_registered) {
    openModal(`<h2>Chọn dữ liệu cần xóa</h2><p class="subtitle"><strong>${esc(employee.name)}</strong> đang sử dụng cả khuôn mặt và vân tay. Chỉ phương thức bạn chọn mới bị xóa; phương thức còn lại vẫn hoạt động.</p><div class="profile-methods"><button class="profile-method" id="deleteFaceChoice" type="button">${icon("user-scan")}<strong>Xóa khuôn mặt</strong><span>Giữ nguyên toàn bộ vân tay đã đăng ký.</span></button><button class="profile-method" id="deleteFingerprintChoice" type="button">${icon("fingerprint")}<strong>Xóa vân tay</strong><span>Chọn mẫu vân tay cần xóa và giữ nguyên khuôn mặt.</span></button></div><div class="modal-actions"><button class="secondary" id="cancelAuthenticationDelete" type="button">Hủy</button></div>`);
    $("#deleteFaceChoice").addEventListener("click", () => openFaceDelete(employee));
    $("#deleteFingerprintChoice").addEventListener("click", () => openFingerprintDeleteChoice(employee));
    $("#cancelAuthenticationDelete").addEventListener("click", closeModal);
    return;
  }
  if (employee.face_registered) {
    openFaceDelete(employee);
    return;
  }
  if (employee.fingerprint_registered) openFingerprintDeleteChoice(employee);
}

$("#addEmployee").addEventListener("click", () => {
  openModal(`<h2>Thêm nhân viên</h2><p class="subtitle">Chọn phương thức xác thực đầu tiên. Có thể bổ sung phương thức còn lại sau trong hồ sơ nhân viên.</p><div class="profile-methods"><button class="profile-method" id="addEmployeeFace" type="button"><strong>Đăng ký khuôn mặt</strong><span>Nhập thông tin và quét khuôn mặt tại camera.</span></button><button class="profile-method" id="addEmployeeFingerprint" type="button"><strong>Đăng ký vân tay</strong><span>Nhập thông tin và làm theo hướng dẫn trên thiết bị.</span></button></div>`);
  $("#addEmployeeFace").addEventListener("click", () => openFaceEnrollment().catch(error => toast(error.message, true)));
  $("#addEmployeeFingerprint").addEventListener("click", () => openFingerprintEnrollment().catch(error => toast(error.message, true)));
});

$("#employeeRows").addEventListener("click", async event => {
  const button = event.target.closest("[data-action]"); if (!button) return;
  const employee = store.employees.data.items.find(item => item.id === button.dataset.id); if (!employee) return;
  if (button.dataset.action === "profile") {
    const faceText = employee.face_registered ? (employee.recognition_enabled ? "Đã đăng ký và đang hoạt động" : "Đã đăng ký nhưng đang tạm khóa") : "Chưa đăng ký";
    const fingerprintText = employee.fingerprint_registered ? `ID ${employee.fingerprint_ids.map(id => `#${id}`).join(", ")}` : "Chưa đăng ký";
    openModal(`<h2>${esc(employee.name)}</h2><p class="subtitle">${esc(employee.id)} · ${esc(employee.role)} · ${esc(employee.department)}</p>${employee.image_url ? `<img class="preview-image" src="${esc(employee.image_url)}" alt="${esc(employee.name)}">` : ""}<div class="profile-methods"><div class="profile-method"><strong>Khuôn mặt</strong><span>${esc(faceText)}</span></div><div class="profile-method"><strong>Vân tay</strong><span>${esc(fingerprintText)}</span></div></div><div class="modal-actions"><button class="secondary" id="closeEmployeeProfile">Đóng</button></div>`);
    $("#closeEmployeeProfile").addEventListener("click", closeModal);
  }
  if (button.dataset.action === "add-face") await openFaceEnrollment(employee);
  if (button.dataset.action === "add-fingerprint") await openFingerprintEnrollment(employee);
  if (button.dataset.action === "manage-fingerprint") openFingerprintManager(employee);
  if (button.dataset.action === "edit") {
    openModal(`<h2>Sửa thông tin nhân viên</h2><p class="subtitle">Thông tin mới sẽ được đồng bộ với dữ liệu nhận diện.</p><form id="editForm"><label>Mã nhân viên<input name="id" value="${esc(employee.id)}" required></label><label>Họ và tên<input name="name" value="${esc(employee.name)}" required></label><label>Chức vụ<input name="role" value="${esc(employee.role)}" required></label><label>Phòng ban<input name="department" value="${esc(employee.department)}" required></label><div class="modal-actions"><button type="button" class="secondary" id="cancelEdit">Hủy</button><button class="primary">Lưu thay đổi</button></div></form>`);
    $("#cancelEdit").addEventListener("click", closeModal); $("#editForm").addEventListener("submit", async e => { e.preventDefault(); try { await api.updateEmployee(employee.id, Object.fromEntries(new FormData(e.target))); closeModal(); store.employees.stale = store.dashboard.stale = store.fingerprints.stale = true; await loadEmployees(); toast("Đã cập nhật hồ sơ và liên kết sinh trắc học"); } catch (error) { toast(error.message, true); } });
  }
  if (button.dataset.action === "toggle") { try { await api.toggleEmployee(employee.id); store.employees.stale = true; await loadEmployees(); } catch (error) { toast(error.message, true); } }
  if (button.dataset.action === "delete-auth") openAuthenticationDelete(employee);
});

let employeeTimer, historyTimer;
$("#employeeSearch").addEventListener("input", event => { employeeRequestVersion++; clearTimeout(employeeTimer); employeeTimer = setTimeout(() => { store.employees.query = event.target.value; store.employees.page = 1; store.employees.stale = true; loadEmployees(); }, 300); });
$("#historySearch").addEventListener("input", event => { clearTimeout(historyTimer); historyTimer = setTimeout(() => { store.history.query = event.target.value; store.history.page = 1; store.history.stale = true; loadHistory(); }, 300); });
$("#historyStatus").addEventListener("change", event => { store.history.status = event.target.value; store.history.page = 1; store.history.stale = true; loadHistory(); });
$("#retryRecognition").addEventListener("click", () => api.retry().catch(error => toast(error.message, true)));
$("#openLiveCamera").addEventListener("click", () => {
  adminLiveViewOpen = true;
  openModal('<h2>Camera trực tiếp tại kiosk</h2><p class="subtitle">Luồng chỉ hoạt động trong khi cửa sổ này đang mở.</p><div class="admin-live-camera" id="adminLiveCamera"></div>');
  $("#adminLiveCamera").append(cameraSurface);
  syncCameraStream();
});

$("#faceEmployeeSelect").addEventListener("change", syncFaceEmployeeFields);
$("#fingerprintEmployeeSelect").addEventListener("change", syncFingerprintEmployeeFields);
$("#cancelFingerprintEnrollment").addEventListener("click", () => {
  clearFingerprintEnrollForm();
  fillNextFingerprintId({ overwrite: true });
  toast("Đã hủy thông tin đăng ký vân tay.");
});
$("#refreshFingerprints").addEventListener("click", () => {
  store.fingerprints.page = 1;
  store.fingerprints.stale = true;
  loadFingerprints(true);
});
$("#fingerprintEnrollForm").addEventListener("submit", async event => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.currentTarget));
  payload.fingerprint_id = Number(payload.fingerprint_id);
  try {
    await api.enrollFingerprint(payload);
    pendingFingerprintEnrollmentId = payload.fingerprint_id;
    clearFingerprintEnrollForm();
    toast(`Đã gửi lệnh đăng ký ID #${payload.fingerprint_id}. Hãy làm theo hướng dẫn trên OLED.`);
  } catch (error) {
    toast(friendlyFingerprintError(error), true);
  }
});

$("#testRecognitionImage").addEventListener("click", () => $("#recognitionImageInput").click());
$("#recognitionImageInput").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  try { await api.testImage(file); } catch (error) { toast(error.message, true); }
});
$("#enrollmentForm").addEventListener("input", () => {
  updateEnrollmentSteps(store.runtime?.enrollment);
  if (store.runtime) updateEnrollmentControls(store.runtime);
  clearTimeout(enrollmentActivityTimer);
  if (store.runtime?.enrollment?.session_active) {
    enrollmentActivityTimer = setTimeout(() => {
      api.noteEnrollmentActivity().catch(error => toast(error.message, true));
    }, 300);
  }
});
$("#startEnrollmentScan").addEventListener("click", async () => {
  const form = $("#enrollmentForm");
  if (!enrollmentFormIsValid()) {
    form.reportValidity();
    toast("Hãy nhập đầy đủ thông tin hợp lệ trước khi bắt đầu quét.", true);
    return;
  }
  try {
    const state = await api.startEnrollmentScan();
    renderStatus(state);
    toast(store.runtime?.enrollment?.session_active ? "Đã bắt đầu quét người tiếp theo." : "Kiosk đã chuyển sang chế độ đăng ký khuôn mặt.");
  } catch (error) {
    toast(error.message, true);
  }
});
$("#cancelEnrollmentScan").addEventListener("click", async event => {
  if (enrollmentCancelPending) return;
  enrollmentCancelPending = true;
  event.currentTarget.disabled = true;
  try {
    const state = await api.cancelEnrollmentScan();
    renderStatus(state);
    toast("Đã hủy lượt quét khuôn mặt. Thông tin nhân viên vẫn được giữ.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    enrollmentCancelPending = false;
    if (store.runtime) updateEnrollmentControls(store.runtime);
  }
});
$("#finishEnrollmentSession").addEventListener("click", async () => {
  try {
    const state = await api.finishEnrollmentSession();
    renderStatus(state);
    toast("Đã kết thúc phiên đăng ký. Kiosk đã trở về chế độ kiểm soát cửa.");
  } catch (error) {
    toast(error.message, true);
  }
});
$("#cancelEnrollmentCapture").addEventListener("click", async event => {
  if (enrollmentCancelPending) return;
  const button = event.currentTarget;
  enrollmentCancelPending = true;
  button.disabled = true;
  try {
    const state = await api.resetEnrollment();
    renderStatus(state);
    toast("Đã hủy ảnh quét. Hãy rời khỏi khung trước khi quét lại.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    enrollmentCancelPending = false;
    button.disabled = false;
  }
});
$("#enrollmentSteps").addEventListener("click", event => {
  const step = event.target.closest(".step");
  if (!step) return;
  const number = Number(step.dataset.step);
  const form = $("#enrollmentForm");
  if (number === 1) {
    const target = [...form.elements].find(field => field.matches?.("input") && !String(field.value || "").trim()) || form.querySelector("input");
    target?.focus();
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  if (number === 2) {
    if (!enrollmentFormIsValid()) {
      form.reportValidity();
      toast("Hãy nhập đầy đủ thông tin hợp lệ trước khi chuyển sang quét khuôn mặt.", true);
      return;
    }
    $(".enrollment-camera-card").focus({ preventScroll: true });
    $(".enrollment-camera-card").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const enrollment = store.runtime?.enrollment || {};
  if (!enrollment.saving && enrollment.state !== "success") {
    toast(enrollment.capture_ready ? "Hãy bấm Lưu khuôn mặt để hoàn tất đăng ký." : "Cần quét khuôn mặt đạt yêu cầu trước khi hoàn tất.", true);
    return;
  }
  $("#enrollmentResult").focus({ preventScroll: true });
  $("#enrollmentResult").scrollIntoView({ behavior: "smooth", block: "center" });
});
$("#enrollmentForm").addEventListener("submit", async event => { event.preventDefault(); try { await api.saveEnrollment(Object.fromEntries(new FormData(event.target))); toast("Đang lưu khuôn mặt..."); } catch (error) { toast(error.message, true); } });

async function onNavigate(route, previous) {
  if (route !== "enrollment") closeCameraFullscreen();
  syncCameraStream();
  if (route === "recognition") {
    moveCamera("recognition");
    if (!kioskView) renderStatus(await api.setMode("attendance"));
    await loadRecent();
  }
  if (route === "enrollment") {
    await loadEnrollmentEmployeeDirectory();
    moveCamera("enrollment");
  }
  if (!kioskView && ["dashboard", "employees", "history"].includes(route) && previous === "enrollment" && store.runtime?.mode === "add_employee") renderStatus(await api.setMode("attendance"));
  if (route === "dashboard") await loadDashboard();
  if (route === "employees") await loadEmployees();
  if (route === "fingerprints") await loadFingerprints();
  if (route === "history") await loadHistory();
}

const navigate = createRouter(onNavigate, kioskView ? { lockedRoute: "recognition" } : {});
document.addEventListener("visibilitychange", () => {
  syncCameraStream();
});
connectStateSocket(renderStatus, connected => {
  const bar = $("#connectionBar");
  bar.classList.toggle("visible", !connected);
  bar.textContent = connected ? "" : "Mất kết nối với Python Core · Đang thử kết nối lại...";
  const sideConnection = $("#sideConnection");
  if (sideConnection) sideConnection.textContent = connected ? "Server đang kết nối" : "Mất kết nối server";
});
api.state().then(renderStatus).catch(error => toast(error.message, true));
navigate(kioskView ? "recognition" : location.hash.slice(1) || "recognition", false).catch(error => toast(error.message, true));
