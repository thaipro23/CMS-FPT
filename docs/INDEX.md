# CMS-FPT — tài liệu theo tác vụ

Nguồn kiểm tra: `c68efc2fcbae18eb1f7266a0fdcfee263d693496`, ngày 15/10/2026.
Đọc [CURRENT_STATE](CURRENT_STATE.md), [CODE_INDEX](../CODE_INDEX.md) hoặc
[REPO_INDEX](REPO_INDEX.md) tùy tác vụ; không nạp toàn bộ tài liệu upstream.

| Tác vụ | Nguồn cần đọc |
|---|---|
| Vai trò Senior/Tech Lead, chất lượng code, hiệu năng, cập nhật context mỗi phiên bản | [ENGINEERING_STANDARDS](ENGINEERING_STANDARDS.md) |
| Connector API/auth/Studio/export | [Connector README](../openedx_connector_plugin/README.md) |
| Quiz submit chậm, guard 0.4.19 | [profiling/invariants/rollout](../openedx_unit_reset/QUIZ_GUARD_PERF_0_4_19.md) |
| Reset, reconciliation, migration 0003 | [0.4.18 rollout](../openedx_unit_reset/DEPLOY_RESET_0_4_18.md); version nguồn hiện tại là 0.4.19, xem snapshot |
| Branding/FEID/custom Learning build | [FPT UI guide](FPT_INDIGO_UI.md); `--restart` có tác động môi trường, chỉ chạy trong scope deploy |
| Assets/provenance | [asset README](../fpt_indigo_ui/assets/README.md) |
| FEID/backend auth | [auth README](../openedx_fpt_auth/README.md) |
| Presence | [presence README](../openedx_fpt_presence/README.md) |
| Legacy courseware import | [hotfix note](HOTFIX_courseware_import.md) — đối chiếu source hiện tại |
| Windows setup mẫu cũ | [INSTALL_LOCAL_WINDOWS](INSTALL_LOCAL_WINDOWS.md), [legacy Unit Reset README](history/UNIT_RESET_README_LEGACY.md) — không dùng như runbook production mới nhất |
| Core Open edX | [upstream docs entry](index.rst), `concepts/`, `how-tos/`, `decisions/`; chỉ mở phần cần |

## Quy tắc cần giữ

- Connector đọc score/progress không chọn lại hay ghi ItemBank; selection thuộc learner runtime.
- Reset chỉ cho `request.user`, có enrollment/Unit scope/CSRF/cooldown; giữ điểm,
  completion và selection của Unit khác. Không coi lookup lỗi là proof để xóa.
- Giữ server timer guard và selected scope; không dùng global cache cho attempt state.
- Reset/next attempt atomic, reconciliation publish sau commit; giữ lock per Unit và course.
- Không reset attempt sinh viên production để kiểm thử. Xác minh migration 0003 trong
  image mới trước khi requests/workers dùng model; backend/worker cùng image.
- FEID và custom Learning/Unit Reset phải còn sau UI rebuild; assets vendor trong Git.
- Giữ core behavior; static CI không thay thế UAT/full image integration/concurrency thật.

## Lịch sử/thiết kế

`docs/history/` và `docs/superpowers/` dùng tra quyết định cũ/proposal; không phải
trạng thái triển khai. Giữ upstream `docs/decisions/` nguyên bản. Dùng
`python scripts/repo_index.py find "tu_khoa" --docs --history --limit 10` hoặc
`--all` khi tìm cả core. Không nhân bản master context theo từng phiên chạy.

| PASS 100% toàn hệ thống | [Runbook/UAT/rollback](PERFECT_PASS_100.md) — feature branch, chưa deploy |
