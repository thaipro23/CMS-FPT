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

## Env bảo mật cho publish user / HMAC

Production không cho anonymous publish và không còn fallback sang "first staff user". Nếu request từ AI Server dùng OAuth client_credentials hoặc HMAC và vào plugin dưới dạng AnonymousUser, CMS container phải có một user staff/admin rõ ràng:

```env
AI_CONNECTOR_PUBLISH_USERNAME=<studio_staff_or_admin_username>
AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH=false
AI_CONNECTOR_HMAC_SECRET=<same_64_hex_secret_as_OPENEDX_CONNECTOR_HMAC_SECRET>
AI_CONNECTOR_HMAC_SKEW_SECONDS=300
AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS=studio.example.edu,lms.example.edu,apps.example.edu
```

`AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH` hiện được giữ để tương thích env cũ nhưng endpoint publish/rollback không còn chấp nhận anonymous. Các endpoint publish/rollback/diagnostics/studio-content yêu cầu một trong hai điều kiện: request có HMAC hợp lệ từ AI Server, hoặc user hiện tại là Studio staff/admin.

Asset/transcript download có SSRF guard: chỉ download từ Studio host hiện tại hoặc host nằm trong `AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS`; redirect bị chặn và cookie chỉ forward cho cùng host.

## Kiểm tra plugin

```bash
curl http://studio.local.openedx.io/api/ai-connector/v1/health
```

Kỳ vọng:

```json
{
  "status": "ok",
  "version": "25.9.13.43",
  "publish_implementation": "content_libraries_v2_python_api",
  "stub_publish": false
}
```

## Lưu ý release Open edX

- Open edX có Content Libraries V2: plugin có thể tạo Library và import Problem thật.
- Open edX chỉ có Legacy Libraries hoặc chưa bật V2: plugin trả lỗi `Content Libraries V2 Python API không khả dụng`. Khi đó cần nâng/bật Libraries V2 hoặc viết adapter Legacy Library riêng.

## v25.9.13.10 - Library component tags

Connector can attach Open edX Content Tags to imported Library problems.

CMS/Studio env vars:

```env
AI_CONNECTOR_TAGGING_ENABLED=true
AI_CONNECTOR_TAG_TAXONOMY_EXPORT_ID=ai-learning-check
AI_CONNECTOR_TAG_TAXONOMY_NAME=AI Learning Check
```

If Content Tagging is unavailable, publishing still succeeds and the connector returns a non-fatal `tag_result` warning.

## Tutor config plugin mode

v25.9.13.43 adds a Tutor plugin helper at:

```text
tutor-plugins/ai_learning_connector_env.py
```

Use it when AI Server and Open edX run separately and you do not want to maintain a manual `docker-compose.override.yml` for `AI_CONNECTOR_*` values. See:

```text
docs/TUTOR_PLUGIN_AI_CONNECTOR_ENV.md
```


## v25.9.16.5.8 - Unified Open edX Connector academic endpoints

The connector also exposes API-first Student Management endpoints under the canonical connector namespace for AI Server:

```http
POST /api/ai-connector/v1/users/resolve
POST /api/ai-connector/v1/courses/search
```

Security uses HMAC headers from AI Server:

```text
X-AI-Connector-Timestamp
X-AI-Connector-Nonce
X-AI-Connector-Signature
```

Use the same connector secret as AI Server `OPENEDX_CONNECTOR_HMAC_SECRET`:

```env
AI_CONNECTOR_HMAC_SECRET=<same-secret-as-AI-server>
AI_CONNECTOR_MAX_BATCH_SIZE=5000
```

`users/resolve` intentionally matches only by exact username (`AP username = CMS/Open edX username`). It does not fuzzy-match by name or email.

## v25.9.16.4.0 - Student Progress Dashboard component grades

`POST /api/ai-connector/v1/class-analytics` now returns best-effort component/subsection grade breakdown when the Open edX deployment has `PersistentSubsectionGrade` rows for the requested users and course.

Response fields per student may include:

```json
{
  "grade_percent": 78.5,
  "component_scores": [
    {"key":"...", "name":"Quiz 1", "earned":8.0, "possible":10.0, "percent":80.0, "category":"subsection"}
  ],
  "grade": {"percent": 78.5, "components": []}
}
```

If component grades are not available, the endpoint still returns user/enrollment/progress/course-grade data and leaves `component_scores` empty.
