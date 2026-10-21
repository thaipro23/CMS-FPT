# Chính sách 100% PASS cho CMS-FPT / Open edX Ulmo.4

**Trạng thái (21/10/2026): source trên feature branch; CHƯA UAT/CHƯA DEPLOY.**

## Invariant nghiệp vụ

- Mọi khóa học cũ/mới/import dùng ngưỡng PASS 100% điểm tổng có trọng số **chưa làm tròn**.
- LMS course grade `passed` và `letter_grade` được tính bằng raw grader percent, thay vì percent 100% đã làm tròn; giá trị display vẫn như Ulmo.4.
- Sai số số thực được chấp nhận ở mức ±1e-12 đơn vị tỉ lệ. Ví dụ 99.5% hoặc 99.99% FAIL; 100% PASS. Điểm >100% vượt tolerance không PASS, cần xác minh dữ liệu.
- Không bắt buộc course progress/completion 100%; đây là hệ thống trạng thái khác điểm tổng. Không ép mọi câu hỏi đều đúng nếu course grader được cấu hình drop_count hoặc trọng số khác 0.
- Grading policy server luôn trả `{'Pass': 1.0}` khi flag bật; CMS API update cutoff chuẩn hóa về ngưỡng duy nhất này. Authoring MFE Ulmo.4 dùng Tutor patch `fpt_indigo_ui/patches/authoring.patch` để thay thanh kéo không hỗ trợ 100% bằng thông báo chính sách cố định; phải build lại MFE để có hiệu lực.
- Tutor plugin `fpt_indigo_ui` mặc định bật `FPT_ENFORCE_PERFECT_PASS=True` trong LMS/CMS/workers. Trên môi trường thiếu plugin/flag mặc định Open edX giữ hành vi cũ.

## Modules

- `xmodule/course_block.py`: getter policy runtime 100% kể cả policy legacy; default khi tạo khóa mới 100% nếu flag bật. Setter không gây ghi ngoài ý muốn khi đọc.
- `cms/djangoapps/models/settings/course_grading.py`: khóa hạ cutoff từ Studio API.
- `lms/djangoapps/grades/course_grade.py`, `xmodule/fpt_perfect_pass.py`: điểm raw quyết định PASS.
- `lms/djangoapps/course_home_api/progress/views.py` và `openedx/core/djangoapps/courseware_api/views.py`: dùng `course_grade.passed` để tránh báo PASS nhầm qua so sánh grade percent đã làm tròn.
- `cms/djangoapps/contentstore/management/commands/fpt_enforce_perfect_pass.py`: audit và batch persistence, sao lưu JSONL được fsync trước từng mutation; dry-run mặc định.

## Quy trình rollout

> Đây là thay đổi trạng thái học tập có thể khiến learner trước đây PASS thành FAIL. Việc backfill chính sách và tái chấm điểm là các bước riêng, không chạy đồng thời hàng loạt.

1. Build riêng LMS/CMS/worker từ cùng commit có code này và kiểm chứng flag trên cả 3 loại tiến trình. Run Open edX pytest/grading tests, MFE build, UAT (điểm 99, 99.5, 99.99, 100; nhiều grader, no graded subsection, course existing/new/import).
2. Backup Mongo modulestore + MySQL grading/Certificate database và xác minh khả năng khôi phục. Xác định số khóa thay đổi trên CMS UAT rồi production (chỉ đọc):

   `./manage.py cms fpt_enforce_perfect_pass --limit 10`

3. Khi triển khai theo batch, sử dụng backup path trên **persistent volume** của pod và đúng SELECTED ở bước audit:

   `./manage.py cms fpt_enforce_perfect_pass --limit 10 --apply --expected-count 10 --backup-file /persistent/backups/pass100-001.jsonl`

   Sau batch chạy lại audit và dùng tên file backup mới cho đợt sau. Command không overwrite backup và không chủ động regrade learner.
4. Regrade từng nhóm khóa với lệnh **có sẵn của Ulmo.4**, chạy từ LMS sau khi tất cả grade workers cùng phiên bản:

   `./manage.py lms compute_grades --courses course-v1:FPL+DOM1021+FA26 --batch_size 25`

   Tác vụ này enqueue Celery nên cần theo dõi queue, kết quả và learner regrade; không chạy `--all_courses` ngay trên production.
5. So trạng thái LMS/Gradebook, certificate, AP/DashCMS và grade sync đối tác sau regrade. Những certificate đã phát hành trước quy định cần quyết định nghiệp vụ về khả năng thu hồi, không tự sửa/chỉnh DB chứng chỉ.
6. Verify audit PENDING=0, kết quả tất cả learner trong course scope đã regrade, không có worker cũ, và đo latency/queue/cpu trước khi kết luận rollout xong.

## Rollback

Tạm ngưng queue regrade. Đồng bộ tắt flag trên LMS/CMS/workers (hoặc restore image trước thay đổi); **điều này không tự khôi phục policy đã migration**. Dùng JSONL cùng snapshot Mongo/MySQL để lập kế hoạch restore từng khóa, review và kiểm thử trên UAT trước khi áp dụng; regrade lại learner và rà certificate. Một snapshot chưa thử restore chưa đủ làm phương án rollback an toàn.

## Các gate đang mở

Cần chạy Python lint/type, full Open edX tests, Authoring MFE validation, UAT với Mongo/MySQL/Celery thật, so baseline grading và theo dõi queue; chưa có kết quả đo hiệu năng hay deploy. Repository catalog phải được rebuild từ committed Git tree (`python scripts/repo_index.py build; python scripts/repo_index.py check`) trước merge; không được suy luận sản phẩm đã triển khai chỉ từ Git commit.
