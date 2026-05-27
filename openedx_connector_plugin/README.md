# CMS AI Connector Plugin

Django app này được cài vào Open edX/Tutor để AI Server không phải phụ thuộc hoàn toàn vào learner-facing Course Blocks API.

## Endpoint quan trọng

```http
GET /api/ai-connector/v1/courses/<course_id>/studio-content
```

Endpoint này đọc từ Studio/modulestore theo hướng draft-first, nhằm lấy:

- HTML raw content trong Studio.
- Problem XML/câu hỏi cũ.
- Course tree.
- Video metadata/transcript fields.
- Link tài liệu đính kèm trong HTML/problem content.
- Course static assets nếu bản Open edX hiện tại hỗ trợ contentstore listing.

## Vì sao cần plugin

Course Blocks API thường chỉ trả learner view đã publish. Với bài toán tạo Learning Check, cần lấy cả bản nháp đang soạn trong Studio, câu hỏi cũ, file đính kèm, PDF/PPTX và thông tin nguồn để AI Server tạo câu hỏi có grounding.

## Các endpoint khác

```http
POST /api/ai-connector/v1/courses/<course_id>/libraries
POST /api/ai-connector/v1/libraries/<library_key>/problems
```

Hai endpoint này giữ contract ensure library/import problem với tag. Hiện vẫn là starter/stub cho local test. Production cần nối thật với CMS Library/Taxonomy API.
