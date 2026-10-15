# CMS-FPT — snapshot nguồn

Đọc trực tiếp Git ngày **15/10/2026 (UTC)** trên `fpt-indigo-ui`, commit
[`c68efc2fcbae18eb1f7266a0fdcfee263d693496`](https://github.com/thaipro23/CMS-FPT/commit/c68efc2fcbae18eb1f7266a0fdcfee263d693496).
Source snapshot, không tự cập nhật theo catalog và không xác nhận deployed image.

| Thành phần | Nguồn đã xác minh | Giới hạn |
|---|---|---|
| Unit Reset | [setup.py](../openedx_unit_reset/setup.py): `0.4.19` | Cần kiểm tra version trong image đang chạy |
| Connector package | [setup.py](../openedx_connector_plugin/setup.py): `0.1.19` | Không đồng nhất với runtime tag |
| Connector runtime | [runtime.py](../openedx_connector_plugin/openedx_ai_connector/runtime.py): `25.9.16.5.109` | Cần đối chiếu runtime thực tế |
| Unit Reset schema | [0003 reconciliation](../openedx_unit_reset/openedx_unit_reset/migrations/0003_unitresetgradereconciliation.py) | Chưa kiểm tra migration DB production trong đợt tổng hợp docs |
| UI build baseline theo guide | Tutor `21.0.9`, Tutor MFE `21.0.2`, Indigo `21.2.1`, `release/ulmo.4` | Baseline tài liệu, không phải kiểm kê runtime vừa chạy |

## Thay đổi và bằng chứng

Guard 0.4.19 tái sử dụng successful XBlock lookup trong một invocation và dùng
read-only SplitMongo `bulk_operations(..., emit_signals=False)` để giảm deserialize
cấu trúc. Không cache lỗi/attempt state giữa request. [Profiling record](../openedx_unit_reset/QUIZ_GUARD_PERF_0_4_19.md)
ghi baseline 13/10: 554 POST `problem_check`, mean 9.96s, p95 39.65s, max 48.65s;
probe một learner timeout ở 10s với 494 `get_item`. Đây là **số liệu trước tối ưu**,
không phải kết quả đã rollout. Chưa chứng minh toàn bộ độ trễ Quiz đã giải quyết.

[Reset/reconciliation 0.4.18 record](../openedx_unit_reset/DEPLOY_RESET_0_4_18.md)
ghi 30 standalone reset tests, 39 Connector tests, 5 read-only selection tests;
Open edX image integration và concurrency PostgreSQL/MySQL thật chưa chạy tại
workspace của record đó. Các số này là bằng chứng nguồn, không phải test ứng dụng
vừa chạy lại khi sửa docs. Fixture/mock không xác minh hết hành vi nền tảng.

## Việc tiếp theo

1. Trước rollout, so cùng read-only guard profile trên UAT với baseline; xác minh
   image chứa Unit Reset 0.4.19, Connector runtime và migration 0003.
2. Acceptance trên test learner: giữ điểm Quiz 1/Final khi reset Quiz 2, đúng selected
   leaves/cooldown/timer/grade reconciliation, thêm lỗi publish/rebuild và concurrency.
3. Theo dõi `problem_check` p50/p95/p99, CPU LMS và 502/504 sau rollout; không tắt timer guard.
4. Custom Learning MFE ngoài repo chưa được đọc ở đợt docs này. Khi sửa UI/MFE phải
   kiểm tra branch `mfe-unit-reset-runtime-clean`, build context và smoke theo
   [FPT UI guide](FPT_INDIGO_UI.md).

Deploy/UAT/merge ổn định cần bằng chứng riêng. Chỉ đổi settings
`UNIT_RESET_QUIZ_GUARD_BULK_READ_ENABLED=False` nếu rollback scoped-read optimization
trong tác vụ vận hành đã xác định; giữ `UNIT_RESET_QUIZ_TIMER_SERVER_GUARD_ENABLED`.
