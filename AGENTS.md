# CMS-FPT: hướng dẫn tiếp tục nhanh

Scope: `thaipro23/CMS-FPT`, nhánh tích hợp `fpt-indigo-ui`.
Đây là Open edX platform có plugin FPT. Không đọc lại core hoặc upstream docs mặc định.

1. Đọc `git status --short`; chạy `python scripts/repo_index.py check`.
2. Đọc `CODE_INDEX.md`, rồi đúng mục trong `docs/INDEX.md`.
3. Tra `python scripts/repo_index.py find "quiz" --limit 10` hoặc `ls <directory>`.
   Catalog có cả core; query mặc định ưu tiên FPT. Dùng `--prefix lms/...` hoặc
   `--all` khi tác vụ cần core. Dùng `rg` trong scope đã chọn, không quét cả platform.
4. `docs/CURRENT_STATE.md` là snapshot nguồn có SHA, không phải xác nhận production.
   Nếu index stale/dirty, xem các đường dẫn đổi rồi làm theo `docs/REPO_INDEX.md`.

Không nạp toàn bộ `docs/_index/catalog.json`, `docs/decisions/`, `docs/history/`,
`docs/superpowers/`, hoặc `README.rst` khi tác vụ chỉ ở plugin FPT.

Ràng buộc: Connector đọc tiến độ không được tạo/chọn lại ItemBank; reset chỉ tác
động selected attempt trong Unit, giữ điểm/completion Unit khác. Giữ server timer
guard; reset/reconciliation dùng lock và publish sau commit. Không lấy reset thật
của sinh viên production làm phép thử. Không bỏ FEID/Unit Reset/custom Learning
build context khi sửa branding. Không merge nhánh ổn định khi chưa đạt UAT yêu cầu.
Source mới không chứng minh image/migration/worker đã mới; cần bằng chứng runtime.

Không tự reset/checkout/pull khi có thay đổi cục bộ. Sau khi đổi module/đường dẫn,
cập nhật bản đồ người đọc; build catalog từ committed source rồi commit catalog.
Chi tiết lệnh và kiểm tra: `docs/REPO_INDEX.md`.
