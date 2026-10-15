# CMS-FPT — bản đồ mã nguồn

Rà soát tại `c68efc2fcbae18eb1f7266a0fdcfee263d693496` (15/10/2026).
Catalog chứa cả core; truy vấn mặc định ưu tiên plugin FPT. Chạy `check` trước khi dùng.

| Tác vụ/thư mục | File đầu vào |
|---|---|
| Connector API/Studio/publish | [urls](openedx_connector_plugin/openedx_ai_connector/urls.py), [views](openedx_connector_plugin/openedx_ai_connector/views.py), [studio](openedx_connector_plugin/openedx_ai_connector/studio.py) |
| Điểm/tiến độ/ItemBank read-only | [student_insight](openedx_connector_plugin/openedx_ai_connector/student_insight.py), [read_consistency](openedx_connector_plugin/openedx_ai_connector/read_consistency.py), [database](openedx_connector_plugin/openedx_ai_connector/database.py) |
| Runtime version/registration | [runtime](openedx_connector_plugin/openedx_ai_connector/runtime.py), [connector setup](openedx_connector_plugin/setup.py), [reset setup](openedx_unit_reset/setup.py) |
| Unit Reset/timer/submit guard | [services](openedx_unit_reset/openedx_unit_reset/services.py), [reset_scope](openedx_unit_reset/openedx_unit_reset/reset_scope.py), [middleware](openedx_unit_reset/openedx_unit_reset/middleware.py), [views](openedx_unit_reset/openedx_unit_reset/views.py) |
| Grade reconciliation/lock/schema | [tasks](openedx_unit_reset/openedx_unit_reset/tasks.py), [models](openedx_unit_reset/openedx_unit_reset/models.py), [migrations](openedx_unit_reset/openedx_unit_reset/migrations/) |
| FEID/auth/identity | [openedx_fpt_auth](openedx_fpt_auth/), [Tutor auth](tutor-plugins/fpt_auth.py) |
| Presence/report proxy | [openedx_fpt_presence](openedx_fpt_presence/), [openedx_fpt_report_proxy](openedx_fpt_report_proxy/) |
| Branding/Authn/Learning runtime | [patches](fpt_indigo_ui/patches/), [vendored assets](fpt_indigo_ui/assets/), [Tutor UI](tutor-plugins/fpt_indigo_ui.py) |
| Build/static/UAT | [setup](scripts/fpt-ui-setup.sh), [build](scripts/fpt-ui-build.sh), [static validator](scripts/fpt-ui-validate-static.sh), [smoke](scripts/fpt-ui-smoke.sh) |
| Redis/MySQL/MinIO/HAProxy integration | [tutor-plugins](tutor-plugins/); tra config/script cụ thể trước thay đổi |
| Plugin regression tests | [connector tests](openedx_connector_plugin/tests/), [Unit Reset tests](openedx_unit_reset/tests/), [UI tests](fpt_indigo_ui/tests/) |

Learning MFE tùy biến ở **repository ngoài** `/opt/openedx/frontend-app-learning`,
branch `mfe-unit-reset-runtime-clean` theo UI guide. Source đó không có trong index
này; cần kiểm tra Git thực tế khi sửa MFE. `frontend-snippets/` chỉ là mẫu cũ.

Core Open edX: `lms/`, `cms/`, `openedx/`, `common/`, `xmodule/`; chỉ mở khi cần.
Ví dụ `python scripts/repo_index.py find "grade" --prefix lms/djangoapps/grades --limit 10`.
Tài liệu/kiểm tra theo tác vụ: [docs/INDEX.md](docs/INDEX.md).
