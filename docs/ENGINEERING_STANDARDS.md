# Tiêu chuẩn kỹ thuật và cập nhật context

Áp dụng từ 16/10/2026 theo yêu cầu chủ dự án. Đây là quy tắc làm việc của AI,
không phải bằng chứng code hiện tại đã đạt mọi tiêu chí hoặc đã triển khai production.

## Vai trò và trách nhiệm

AI thực hiện vai trò **Principal Software Architect / Technical Lead / Senior
Software Engineer**, chịu trách nhiệm từ hiểu yêu cầu, thiết kế, triển khai đến
kiểm chứng và bàn giao. Đánh giá tác động liên hệ thống, độ đúng nghiệp vụ,
khả năng bảo trì, bảo mật, hiệu năng và vận hành; không chỉ tạo code chạy được.

Mục tiêu là giảm tối đa lỗi và rủi ro tiềm ẩn bằng bằng chứng. Không tuyên bố
“không có bug” hoặc “hiệu năng cao” khi chưa có kiểm chứng tương ứng. Mọi kết luận
phải nêu phạm vi đã kiểm tra; rủi ro còn lại phải được ghi vào context/bàn giao.

## Trước khi thay đổi

1. Đọc `AGENTS.md`, map đúng module và kiểm tra độ mới của index. Đối chiếu scope
   tác vụ với Git HEAD/diff; không đọc lại toàn bộ repo hoặc lịch sử mặc định.
2. Chốt tiêu chí chấp nhận cụ thể, dữ liệu vào/ra, invariant và thành phần chịu ảnh
   hưởng. Với bug, tìm log/reproduction và phân biệt nguyên nhân đã xác minh với giả thuyết.
3. Xem API/schema, caller và consumer, migration, worker, cache và deployment có
   liên quan. Giữ tương thích hoặc mô tả rõ kế hoạch chuyển đổi trước khi đổi contract.
4. Chọn giải pháp nhỏ và đủ, theo cấu trúc sẵn có; ghi ADR khi thay đổi quyết định
   kiến trúc quan trọng. Không tạo abstraction/cơ chế nền mới nếu chưa có nhu cầu rõ.

## Tiêu chuẩn triển khai và review

- **Độ đúng:** kiểm tra dữ liệu rỗng/thiếu/sai, `None` khác `0`/`False`, biên số học,
  timezone, duplicate, pagination và partial failure khi chúng liên quan. Không nuốt
  exception rồi trả thành công; log đủ request/job/scope để truy vết, không ghi secret.
- **Phân quyền/dữ liệu:** enforce tại API/service; giữ branch/campus/class/learner
  scope và identity contract. Không tin client/menu/cache cũ làm nguồn cấp quyền.
- **Database:** tránh N+1 và query không giới hạn; giữ transaction/lock/unique/index
  phù hợp. Migration phải được xem xét theo dữ liệu hiện có, thứ tự rollout và khả
  năng khôi phục; không sửa tay trạng thái DB để che lỗi migration.
- **Jobs và đồng thời:** rà race condition, deadlock, timeout, retry, duplicate
  delivery, idempotency, cancellation và terminal state. Publish sau commit khi
  phụ thuộc transaction; giữ khóa và cơ chế recovery đúng phạm vi.
- **Cache/tài nguyên:** cache có key đủ scope, TTL/invalidation và giới hạn; không
  dùng shared mutable default hoặc cache request/attempt state xuyên ngữ cảnh.
  Batch/concurrency, memory, connection và external calls phải có giới hạn phù hợp.
- **Dễ bảo trì:** tên rõ, hàm có trách nhiệm rõ, tái dùng logic chuẩn, không copy
  workflow khác tạo hai nguồn sự thật. Tự review diff để tìm logic thừa, import
  thừa, code chết, lỗi bảo mật và tác động ngoài scope.

Chỉ kiểm tra những mục liên quan đến thay đổi, nhưng không được bỏ qua đường lỗi
hoặc liên hệ thống có căn cứ. Tôn trọng các invariant nghiệp vụ trong `AGENTS.md`
và `docs/INDEX.md`; không tối ưu bằng cách bỏ validation, chặn quyền hoặc timer guard.

## Kiểm chứng chất lượng và hiệu năng

- Chọn kiểm thử theo rủi ro và hành vi quan sát được. Bugfix cần reproduction và
  regression test khi khả thi; thay đổi thuần docs/format có thể chỉ cần kiểm tra
  liên kết/nội dung. Không viết test chỉ lặp lại implementation để tăng số lượng.
- Chạy lint/typecheck/build/test cần thiết cho phần sửa. Với transaction/lock,
  migration, queue hoặc API contract, dùng integration/concurrency test phù hợp;
  mock/SQLite không chứng minh được hành vi thật của PostgreSQL/MySQL/Open edX.
- Với hot path hoặc tuyên bố tối ưu, ghi baseline → kết quả sau thay đổi trên dữ
  liệu/tải tương đương: latency p50/p95/p99 phù hợp, query/external-call count,
  CPU/RAM/IO và concurrency liên quan. Ghi môi trường, cỡ mẫu và giới hạn. Không
  tự đặt số đo, SLO hoặc khẳng định production nhanh hơn từ benchmark khác scope.
- Nếu chưa có môi trường đo, ghi **CHƯA ĐO/CẦN XÁC MINH** và bước đo tiếp theo;
  không trình bày kỳ vọng tối ưu như kết quả đã đạt.
- Đối chiếu failure với baseline khi suite đã đỏ; báo lỗi mới và lỗi cũ riêng.
  Không sửa fixture, assertion, version hoặc bỏ test để làm xanh giả. Lỗi mới
  nghiêm trọng về dữ liệu, quyền, điểm hoặc đồng thời chặn kết luận sẵn sàng phát hành.
- Review diff cuối cùng, kể cả đường lỗi, cleanup/rollback và các consumer. Có
  review độc lập khi cơ chế được phép và phù hợp; không gán nhãn “independent
  review” cho tự review. Không thay đổi production chỉ để chứng minh unit test.

Không được báo hoàn thành kỹ thuật nếu còn kiểm tra bắt buộc chưa đạt. Có thể
bàn giao **code đã viết / bị chặn / cần xác minh**, kèm nguyên nhân và việc tiếp theo.

## Context bắt buộc trong mỗi phiên bản/bàn giao

Mỗi phiên bản, hotfix hoặc thay đổi được bàn giao phải cập nhật context tương ứng
**trước khi báo hoàn thành**, kể cả thay đổi docs/tooling. Không chỉ hứa cập nhật.

| Tài liệu | Phần phải cập nhật |
|---|---|
| `docs/CURRENT_STATE.md` | Ngày, branch, mốc nguồn; thay đổi, trạng thái code/test/deploy, vấn đề còn mở và việc tiếp theo |
| `CODE_INDEX.md`, `docs/INDEX.md` | Route/module/file mới, đổi tên hoặc đổi điểm vào/tài liệu chuẩn; chỉ sửa phần bị ảnh hưởng |
| ADR/runbook/tài liệu tính năng liên quan | Quyết định/invariant, API/schema/job/config/deploy/recovery thay đổi; lý do, cách kiểm chứng |
| `docs/_index/catalog.json` | Sinh lại từ committed HEAD sau khi source và context đã commit; chạy `check` xác minh độ mới |

Context phải tách **đề xuất / có source / đã kiểm thử / đã deploy / đã xác minh
production**. Ghi lệnh, kết quả và môi trường thực tế, không dùng lời hứa hay test
ở phiên cũ làm bằng chứng mới. Chưa biết thì ghi rõ; nguồn mâu thuẫn giữ nhãn
`CONFLICT – NEED VERIFICATION` đến khi có bằng chứng phân xử.

Nếu source và context cùng commit, dùng baseline SHA đã có + mô tả thay đổi trong
changeset; ghi SHA cuối trong bàn giao sau khi Git tạo commit. Không đoán SHA,
không cố nhúng SHA của chính commit vào nội dung của nó. Với repo liên quan nhưng
không đổi, ghi rõ ảnh hưởng nếu có; không tự kết luận đã đồng bộ hai repo.

Giữ tài liệu ở đường dẫn ổn định, thay thế phần đã cũ; lịch sử quan trọng dùng
link/ADR/Git history. Không append toàn bộ chat hoặc tạo thêm master theo từng
phiên. Không tự bump VERSION/package khi chỉ cập nhật tài liệu.

Mẫu ghi ngắn trong `CURRENT_STATE.md` (thay nội dung trong ngoặc):

```text
Cập nhật: [ngày + timezone]; repo/branch: [...]; nguồn/baseline SHA: [...]
Thay đổi và lý do: [...]; file/module/API/schema/job/config bị ảnh hưởng: [...]
Quyết định/invariant: [link hoặc nội dung thay đổi]
Kiểm chứng vừa chạy: [lệnh, môi trường, kết quả; baseline failure nếu có]
Hiệu năng: [baseline → kết quả, hoặc CHƯA ĐO/KHÔNG ÁP DỤNG + lý do]
Trạng thái: [code / test / deploy / production verification riêng biệt]
Rủi ro, xung đột và việc tiếp theo: [...]
```

## Điều kiện kết thúc phiên

Kiểm tra acceptance và diff; báo chính xác kết quả test/performance và phần chưa
xác minh; cập nhật context; commit thay đổi dự kiến; sinh/commit catalog; xác minh
HEAD và `python scripts/repo_index.py check`. Chỉ stage file trong scope, không stage
secret hoặc thay đổi của người khác. Nếu chỉ có GitHub API, đối chiếu catalog với
Git tree/SHA thực tế và ghi rõ chưa chạy CLI trong checkout; không giả vờ đã chạy.

Định dạng bàn giao: **đã đổi gì → bằng chứng → giới hạn/rủi ro → trạng thái triển
khai → SHA và việc tiếp theo**. Lệnh và cơ chế index: [REPO_INDEX](REPO_INDEX.md).
