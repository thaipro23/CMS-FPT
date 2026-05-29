# CMS AI Connector Plugin

Django app này được cài vào Open edX/Tutor để AI Server không phải phụ thuộc hoàn toàn vào learner-facing Course Blocks API.

## Endpoint đọc học liệu

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

## Endpoint publish thật từ v25.9.13.4

```http
POST /api/ai-connector/v1/courses/<course_id>/libraries
POST /api/ai-connector/v1/libraries/<library_key>/problems
POST /api/ai-connector/v1/courses/<course_id>/problems
```

Từ v25.9.13.4, các endpoint này không còn dùng stub/local memory. Chúng thử dùng Content Libraries V2 Python API trong CMS/Studio:

- `create_library`
- `get_library`
- `create_library_block`
- `set_library_block_olx`
- `publish_component_changes`
- `publish_changes`

Nếu Open edX đang chạy chưa có Content Libraries V2 API hoặc chưa xác định được Studio staff user để publish, plugin sẽ trả lỗi rõ ràng và **không báo thành công giả**.

## Env cho publish user

Nếu request từ AI Server dùng OAuth client_credentials và vào plugin dưới dạng AnonymousUser, set user staff/admin cho CMS container:

```env
AI_CONNECTOR_PUBLISH_USERNAME=<studio_staff_or_admin_username>
AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH=false
```

Không bật `AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH=true` trong production.

## Kiểm tra plugin

```bash
curl http://studio.local.openedx.io/api/ai-connector/v1/health
```

Kỳ vọng:

```json
{
  "status": "ok",
  "version": "25.9.13.4",
  "publish_implementation": "content_libraries_v2_python_api",
  "stub_publish": false
}
```

## Lưu ý release Open edX

- Open edX có Content Libraries V2: plugin có thể tạo Library và import Problem thật.
- Open edX chỉ có Legacy Libraries hoặc chưa bật V2: plugin trả lỗi `Content Libraries V2 Python API không khả dụng`. Khi đó cần nâng/bật Libraries V2 hoặc viết adapter Legacy Library riêng.
