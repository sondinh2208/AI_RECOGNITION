import { store } from "./state.js";

const meta = {
  dashboard: ["Tổng quan", "Tổng quan hoạt động của hệ thống quản lý ra vào", "#i-home"],
  enrollment: ["Đăng ký khuôn mặt", "Thêm thông tin nhân viên và khuôn mặt để cấp quyền mở cửa", "#i-user-scan"],
  recognition: ["Giám sát cửa", "Theo dõi xác thực, thiết bị và yêu cầu mở khóa theo thời gian thực", "#i-target"],
  employees: ["Nhân viên", "Quản lý hồ sơ, khuôn mặt, vân tay và quyền ra vào", "#i-users"],
  fingerprints: ["Quản lý vân tay", "Đăng ký và xóa từng mẫu vân tay trên bộ điều khiển cửa", "#i-fingerprint"],
  history: ["Nhật ký xác thực", "Tra cứu các lượt xác thực khuôn mặt, vân tay và nút mở cửa", "#i-list"],
};

export function createRouter(onNavigate, { lockedRoute = null } = {}) {
  const main = document.querySelector(".main");
  const navigate = async (route, push = true) => {
    if (lockedRoute) route = lockedRoute;
    if (!meta[route]) route = "recognition";
    const previous = store.route;
    store.scroll.set(previous, main.scrollTop);
    store.route = route;
    document.querySelectorAll("[data-page]").forEach(page => page.classList.toggle("active", page.dataset.page === route));
    document.querySelectorAll(".nav [data-route]").forEach(button => button.classList.toggle("active", button.dataset.route === route));
    const [title, subtitle, icon] = meta[route];
    document.querySelector("#pageTitle").textContent = title;
    document.querySelector("#pageSubtitle").textContent = subtitle;
    document.querySelector(".page-icon use").setAttribute("href", icon);
    if (push) history.pushState({ route }, "", `#${route}`);
    main.scrollTop = store.scroll.get(route) || 0;
    await onNavigate(route, previous);
  };
  document.addEventListener("click", event => {
    const target = event.target.closest("[data-route]");
    if (target) { event.preventDefault(); navigate(target.dataset.route); }
  });
  addEventListener("popstate", event => navigate(event.state?.route || location.hash.slice(1), false));
  return navigate;
}
