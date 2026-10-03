import { api, connectStateSocket } from "./bridge.js";
import { createRouter } from "./router.js";
import { markDataStale, store } from "./state.js";

const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
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
cameraImage.addEventListener("load", () => cameraSurface.classList.add("loaded"));
cameraImage.src = `/api/camera/stream?t=${Date.now()}`;

function moveCamera(route) {
  const slot = document.querySelector(`[data-camera-slot="${route}"]`);
  if (slot && cameraSurface.parentElement !== slot) slot.append(cameraSurface);
}

function renderStatus(state) {
  const recognitionChanged = !store.runtime || store.runtime.version !== state.version;
  store.runtime = state;
  markDataStale(state.history_version);
  $("#sideCamera").textContent = state.camera.running ? "Hoạt động" : "Ngoại tuyến";
  $("#sideDevice").textContent = state.camera.device === "cpu" ? "CPU" : "GPU sẵn sàng";
  $("#enrollmentFps").textContent = Math.round(state.camera.fps || 0);
  $("#enrollmentCameraStatus").textContent = state.enrollment.capture_ready ? "Đã quét khuôn mặt · Sẵn sàng lưu" : state.enrollment.status_text;
  $("#saveEnrollment").disabled = !state.enrollment.capture_ready || state.enrollment.saving;
  $("#saveEnrollment").textContent = state.enrollment.saving ? "Đang lưu khuôn mặt..." : "Lưu khuôn mặt";
  $("#enrollmentNotice").textContent = state.enrollment.message || state.enrollment.status_text;
  if (recognitionChanged) renderRecognition(state.recognition);
  renderRecognitionTest(state.recognition_test);
  if (store.route === "recognition" && store.history.stale) loadRecent();
}

function renderRecognitionTest(test) {
  if (!test) return;
  const button = $("#testRecognitionImage");
  button.disabled = test.state === "processing";
  button.textContent = test.state === "processing" ? "Đang phân tích ảnh..." : "Kiểm thử bằng file ảnh";
  if (test.version <= store.recognitionTestVersion || !["complete", "error"].includes(test.state)) return;
  store.recognitionTestVersion = test.version;
  if (test.error) { toast(test.error, true); return; }
  const result = test.result;
  openModal(`<h2>Kết quả kiểm thử ảnh</h2><p class="subtitle">${esc(result.file)} · Không ghi vào lịch sử điểm danh</p><div class="test-result"><strong>${esc(result.conclusion)}</strong><p>Top 1: ${esc(result.best.name || "Không rõ")} (${esc(result.best.id || "---")}) · Distance ${esc(result.best_distance)}</p><p>Top 2: ${esc(result.second.name || "Không có")} (${esc(result.second.id || "---")}) · Distance ${esc(result.second_distance ?? "---")}</p><p>Margin: ${esc(result.margin)}</p></div><div class="modal-actions"><button class="primary" id="closeTestResult">Đóng</button></div>`);
  $("#closeTestResult").addEventListener("click", closeModal);
}

function renderRecognition(result) {
  const panel = $("#recognitionResult"); panel.dataset.state = result.state;
  const iconName = ["success", "duplicate"].includes(result.state) ? "check" : result.state === "ambiguous" ? "alert" : result.state === "unknown" ? "x" : "target";
  panel.querySelector(".result-icon use").setAttribute("href", `#i-${iconName}`);
  panel.querySelector(".result-header h2").textContent = result.title;
  panel.querySelector(".result-header p").textContent = result.message;
  const employee = result.employee;
  panel.querySelector(".employee-summary").innerHTML = employee ? `<img class="avatar" src="${esc(employee.image_url)}" alt="${esc(employee.name)}"><div><h3>${esc(employee.name)}</h3><p>Mã NV: <b>${esc(employee.id)}</b></p><p>Chức vụ: ${esc(employee.role)}</p><p>Phòng ban: ${esc(employee.department)}</p></div>` : `<div class="avatar placeholder">${icon("users")}</div><div><h3>${result.state === "unknown" ? "Người lạ / Khách" : result.state === "ambiguous" ? "Chưa xác định" : "Chưa có lượt quét"}</h3><p>Mã NV: <b>---</b></p><p>Chức vụ: ---</p><p>Phòng ban: ---</p></div>`;
  const notice = panel.querySelector(".result-notice");
  notice.querySelector("strong").textContent = result.state === "ambiguous" ? "Khuôn mặt không khớp đủ tin cậy với hồ sơ đã đăng ký." : result.state === "unknown" ? "Khuôn mặt chưa có trong hệ thống dữ liệu." : employee ? `Chào mừng ${employee.name}.` : "Đưa đầy đủ khuôn mặt vào khung để điểm danh.";
  notice.querySelector("span").textContent = ["ambiguous", "unknown"].includes(result.state) ? "Không ghi nhận điểm danh thành công." : "Chúc bạn một ngày làm việc hiệu quả!";
  panel.querySelector(".result-time b").textContent = result.timestamp || "Chờ quét...";
  const retry = $("#retryRecognition"); retry.hidden = !["ambiguous", "unknown", "error"].includes(result.state);
  panel.querySelector(".status-action").hidden = !retry.hidden;
}

function rowsHtml(rows) {
  if (!rows.length) return `<tr><td class="empty" colspan="6">Chưa có dữ liệu</td></tr>`;
  return rows.map(row => `<tr><td>${esc(row.time)}</td><td><strong>${esc(row.name)}</strong></td><td>${esc(row.id)}</td><td>${esc(row.role)}</td><td>${esc(row.dept)}</td><td><span class="status-text ${success(row.status) ? "success" : "failed"}">${esc(row.status)}</span></td></tr>`).join("");
}

async function loadRecent() {
  try { const data = await api.history({ page: 1, pageSize: 5 }); replaceHtml($("#recentAttendance"), rowsHtml(data.items)); store.history.stale = false; } catch (error) { toast(error.message, true); }
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
      dashboardMetric({ tone: "green", iconName: "monitor", label: "Kiosk Điểm danh", value: online ? "Hoạt động" : "Sẵn sàng", note: online ? "Camera đang kết nối" : "Sẵn sàng phục vụ", decoration: "pulse" }),
      dashboardMetric({ tone: "blue", iconName: "users", label: "Người đăng ký", value: `${data.employees} hồ sơ`, note: "Đã đăng ký trong hệ thống" }),
      dashboardMetric({ tone: "orange", iconName: "clock", label: "Lượt điểm danh", value: `${data.today_total} lượt`, note: "Trong ngày hôm nay" }),
      dashboardMetric({ tone: "violet", iconName: "target", label: "Tỷ lệ thành công", value: `${data.success_rate}%`, note: "Trong ngày hôm nay", decoration: "wave" }),
    ].join(""));
    const max = Math.max(1, ...data.week.map(day => day.value));
    const currentWeekday = (new Date().getDay() + 6) % 7;
    replaceHtml($("#weekChart"), `<div class="chart-grid"><span>50</span><span>40</span><span>30</span><span>20</span><span>10</span><span>0</span></div><div class="chart-bars">${data.week.map((day, index) => `<div class="chart-column"><div class="chart-value">${day.value}</div><div class="chart-bar-track"><i class="chart-bar-fill${index === currentWeekday ? " current" : ""}" style="height:${Math.max(3, day.value / max * 100)}%"></i></div><strong>${esc(day.label)}</strong><span>${esc(day.date)}</span></div>`).join("")}</div>`);
    replaceHtml($("#dashboardRecent"), data.recent.length ? data.recent.map(row => {
      const ok = success(row.status);
      return `<div class="timeline-item ${ok ? "ok" : "fail"}"><span class="timeline-dot"></span><span class="timeline-avatar">${icon(ok ? "check" : "x")}</span><div class="timeline-copy"><strong>${esc(row.name)}</strong><span>${ok ? "Điểm danh thành công" : esc(row.reason || "Nhận diện không thành công")}</span></div><time>${esc(relativeTime(row.time))}</time><span class="timeline-status">${esc(row.status)}</span></div>`;
    }).join("") : `<div class="empty">Chưa có hoạt động</div>`);
    const failureRate = Math.max(0, 100 - data.success_rate);
    replaceHtml($("#successOverview"), `<div class="success-donut" style="--rate:${data.success_rate}"><div><strong>${data.success_rate}%</strong><span>Thành công</span></div></div><div class="success-legend"><div><i class="legend-blue"></i><span>Điểm danh thành công</span><strong>${data.success_rate}%<small>${data.today_success} lượt</small></strong></div><div><i class="legend-red"></i><span>Chưa thành công</span><strong>${failureRate}%<small>${data.today_failed} lượt</small></strong></div></div>`);
  } catch (error) { toast(error.message, true); }
}

function pagination(element, data, onPage) {
  const buttons = [`<button ${data.page <= 1 ? "disabled" : ""} data-p="${data.page - 1}">‹</button>`];
  for (let page = 1; page <= data.pages; page++) if (data.pages <= 7 || page === 1 || page === data.pages || Math.abs(page - data.page) <= 1) buttons.push(`<button class="${page === data.page ? "active" : ""}" data-p="${page}">${page}</button>`);
  buttons.push(`<button ${data.page >= data.pages ? "disabled" : ""} data-p="${data.page + 1}">›</button>`);
  element.innerHTML = buttons.join(""); element.querySelectorAll("button:not([disabled])").forEach(button => button.addEventListener("click", () => onPage(Number(button.dataset.p))));
}

async function loadEmployees(force = false) {
  const state = store.employees; if (!force && !state.stale && state.data) return;
  try {
    const data = await api.employees({ query: state.query, page: state.page, pageSize: state.pageSize }); state.data = data; state.page = data.page; state.stale = false;
    $("#employeeStats").innerHTML = statCard("Tổng nhân viên", data.all_total, "users") + statCard("Đang nhận diện", data.enabled_total, "check") + statCard("Trang hiện tại", `${data.page}/${data.pages}`, "list");
    replaceHtml($("#employeeRows"), data.items.length ? data.items.map(item => `<tr><td><img class="row-avatar" loading="lazy" src="${item.image_url}" alt=""></td><td>${esc(item.id)}</td><td><strong>${esc(item.name)}</strong></td><td>${esc(item.role)}</td><td>${esc(item.department)}</td><td><span class="status-text ${item.recognition_enabled ? "success" : "failed"}">${item.recognition_enabled ? "Đang nhận diện" : "Chỉ kiểm thử"}</span></td><td><div class="actions"><button class="icon-button" data-action="view" data-id="${esc(item.id)}" aria-label="Xem ảnh">${icon("eye")}</button><button class="icon-button" data-action="edit" data-id="${esc(item.id)}" aria-label="Sửa">${icon("edit")}</button><button class="icon-button" data-action="toggle" data-id="${esc(item.id)}" aria-label="Bật tắt">${icon("target")}</button><button class="icon-button danger" data-action="delete" data-id="${esc(item.id)}" aria-label="Xóa">${icon("trash")}</button></div></td></tr>`).join("") : `<tr><td class="empty" colspan="7">Không tìm thấy nhân viên</td></tr>`);
    pagination($("#employeePagination"), data, page => { state.page = page; state.stale = true; loadEmployees(); });
  } catch (error) { toast(error.message, true); }
}

async function loadHistory(force = false) {
  const state = store.history; if (!force && !state.stale && state.data) return;
  try { const data = await api.history({ query: state.query, status: state.status, page: state.page, pageSize: state.pageSize }); state.data = data; state.page = data.page; state.stale = false; replaceHtml($("#historyRows"), rowsHtml(data.items)); pagination($("#historyPagination"), data, page => { state.page = page; state.stale = true; loadHistory(); }); } catch (error) { toast(error.message, true); }
}

const modal = $("#modal");
let focusBeforeModal = null;
function closeModal() {
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

$("#employeeRows").addEventListener("click", async event => {
  const button = event.target.closest("[data-action]"); if (!button) return;
  const employee = store.employees.data.items.find(item => item.id === button.dataset.id); if (!employee) return;
  if (button.dataset.action === "view") openModal(`<h2>${esc(employee.name)}</h2><p class="subtitle">${esc(employee.id)} · ${esc(employee.role)} · ${esc(employee.department)}</p><img class="preview-image" src="${employee.image_url}" alt="${esc(employee.name)}">`);
  if (button.dataset.action === "edit") {
    openModal(`<h2>Sửa thông tin nhân viên</h2><p class="subtitle">Thông tin mới sẽ được đồng bộ với dữ liệu nhận diện.</p><form id="editForm"><label>Mã nhân viên<input name="id" value="${esc(employee.id)}" required></label><label>Họ và tên<input name="name" value="${esc(employee.name)}" required></label><label>Chức vụ<input name="role" value="${esc(employee.role)}" required></label><label>Phòng ban<input name="department" value="${esc(employee.department)}" required></label><div class="modal-actions"><button type="button" class="secondary" id="cancelEdit">Hủy</button><button class="primary">Lưu thay đổi</button></div></form>`);
    $("#cancelEdit").addEventListener("click", closeModal); $("#editForm").addEventListener("submit", async e => { e.preventDefault(); try { await api.updateEmployee(employee.id, Object.fromEntries(new FormData(e.target))); closeModal(); store.employees.stale = store.dashboard.stale = true; await loadEmployees(); toast("Đã cập nhật thông tin nhân viên"); } catch (error) { toast(error.message, true); } });
  }
  if (button.dataset.action === "toggle") { try { await api.toggleEmployee(employee.id); store.employees.stale = true; await loadEmployees(); } catch (error) { toast(error.message, true); } }
  if (button.dataset.action === "delete") {
    openModal(`<h2>Xóa nhân viên?</h2><p class="subtitle">Hồ sơ <strong>${esc(employee.name)}</strong> và dữ liệu khuôn mặt sẽ bị xóa. Thao tác này không thể hoàn tác.</p><div class="modal-actions"><button class="secondary" id="cancelDelete">Hủy</button><button class="primary" id="confirmDelete">Xóa nhân viên</button></div>`);
    $("#cancelDelete").addEventListener("click", closeModal); $("#confirmDelete").addEventListener("click", async () => { try { await api.deleteEmployee(employee.id); closeModal(); store.employees.stale = store.dashboard.stale = true; await loadEmployees(); toast("Đã xóa nhân viên"); } catch (error) { toast(error.message, true); } });
  }
});

let employeeTimer, historyTimer;
$("#employeeSearch").addEventListener("input", event => { clearTimeout(employeeTimer); employeeTimer = setTimeout(() => { store.employees.query = event.target.value; store.employees.page = 1; store.employees.stale = true; loadEmployees(); }, 300); });
$("#historySearch").addEventListener("input", event => { clearTimeout(historyTimer); historyTimer = setTimeout(() => { store.history.query = event.target.value; store.history.page = 1; store.history.stale = true; loadHistory(); }, 300); });
$("#historyStatus").addEventListener("change", event => { store.history.status = event.target.value; store.history.page = 1; store.history.stale = true; loadHistory(); });
$("#retryRecognition").addEventListener("click", () => api.retry().catch(error => toast(error.message, true)));
$("#testRecognitionImage").addEventListener("click", () => $("#recognitionImageInput").click());
$("#recognitionImageInput").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  try { await api.testImage(file); } catch (error) { toast(error.message, true); }
});
$("#enrollmentForm").addEventListener("submit", async event => { event.preventDefault(); try { await api.saveEnrollment(Object.fromEntries(new FormData(event.target))); toast("Đang lưu khuôn mặt..."); } catch (error) { toast(error.message, true); } });

async function onNavigate(route) {
  if (route === "recognition") { moveCamera("recognition"); await api.setMode("attendance"); await loadRecent(); }
  if (route === "enrollment") { moveCamera("enrollment"); await api.setMode("add_employee"); }
  if (route === "dashboard") { await api.setMode("idle"); await loadDashboard(); }
  if (route === "employees") { await api.setMode("idle"); await loadEmployees(); }
  if (route === "history") { await api.setMode("idle"); await loadHistory(); }
}

const navigate = createRouter(onNavigate);
connectStateSocket(renderStatus, connected => { const bar = $("#connectionBar"); bar.classList.toggle("visible", !connected); bar.textContent = connected ? "" : "Mất kết nối với Python Core · Đang thử kết nối lại..."; });
api.state().then(renderStatus).catch(error => toast(error.message, true));
navigate(location.hash.slice(1) || "recognition", false).catch(error => toast(error.message, true));
