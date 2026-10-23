// Đặt giao diện sáng/tối trước khi vẽ trang để tránh nháy màu.
(function () {
  try {
    var t = localStorage.getItem("docqa:theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* localStorage bị chặn: theo hệ điều hành */ }
})();
