"""
==============================================================
COMMON MIXIN
Các tiện ích, phương thức dùng chung giữa các luồng.
==============================================================
"""


class CommonMixin:
    """Mixin chứa các hàm helper dùng chung cho toàn bộ ứng dụng."""

    def _safe_after(self, delay_ms, func, *args):
        """Gọi after một cách an toàn giữa các luồng (Thread-safe)."""
        try:
            if hasattr(self, 'tk') and self.winfo_exists():
                return self.after(delay_ms, func, *args)
        except Exception:
            pass
        return None
