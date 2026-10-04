import { store } from "./state.js";

const meta = {
  dashboard: ["Bảng điều khiển", "Tổng quan hoạt động điểm danh và quản lý nhân sự", "#i-home"],
  enrollment: ["Đăng ký khuôn mặt", "Thêm thông tin nhân viên và ghi nhận khuôn mặt để sử dụng điểm danh", "#i-user-scan"],
  recognition: ["Nhận diện điểm danh", "Quét khuôn mặt để ghi nhận thời gian vào/ra", "#i-target"],
  employees: ["Người đăng ký", "Quản lý danh sách nhân viên đã đăng ký nhận diện khuôn mặt", "#i-users"],
  history: ["Lịch sử ra vào", "Xem toàn bộ dữ liệu ra vào của nhân sự", "#i-list"],
};

export function createRouter(onNavigate) {
  const main = document.querySelector(".main");
  const navigate = async (route, push = true) => {
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
    await onNavigate(route);
  };
  document.addEventListener("click", event => {
    const target = event.target.closest("[data-route]");
    if (target) { event.preventDefault(); navigate(target.dataset.route); }
  });
  addEventListener("popstate", event => navigate(event.state?.route || location.hash.slice(1), false));
  return navigate;
}
