# CMS-FPT — Open edX và tích hợp FPT

Nhánh tích hợp: `fpt-indigo-ui`. Repository chứa Open edX platform, Connector cho
Dash CMS, Unit Reset/timed Quiz, FEID/auth, presence/report proxy và UI FPT qua Tutor.

- [AGENTS.md](AGENTS.md): quy trình đọc có scope cho AI.
- [CODE_INDEX.md](CODE_INDEX.md): bản đồ module và file đầu vào.
- [docs/INDEX.md](docs/INDEX.md): tài liệu theo tác vụ.
- [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md): snapshot nguồn có SHA.
- [docs/REPO_INDEX.md](docs/REPO_INDEX.md): chỉ mục file/thư mục và kiểm tra độ mới.

```sh
python scripts/repo_index.py check
python scripts/repo_index.py find "reset" --limit 10
python scripts/repo_index.py ls openedx_connector_plugin/openedx_ai_connector
```

Snapshot 15/10/2026: Unit Reset source `0.4.19`, Connector package `0.1.19`,
runtime `25.9.16.5.109`; đây không phải xác nhận image production đang dùng các bản này.
Deployment/UAT: [UI build guide](docs/FPT_INDIGO_UI.md),
[Unit Reset migration](openedx_unit_reset/DEPLOY_RESET_0_4_18.md),
[guard 0.4.19](openedx_unit_reset/QUIZ_GUARD_PERF_0_4_19.md).

README trước đây chỉ mô tả gói Unit Reset đời cũ; giữ tại
[docs/history/UNIT_RESET_README_LEGACY.md](docs/history/UNIT_RESET_README_LEGACY.md).
Tài liệu Open edX upstream vẫn ở [README.rst](README.rst) và `docs/`; chỉ mở khi tác vụ cần core.
