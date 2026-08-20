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
AI_CONNECTOR_HMAC_SECRET=<same_secret_as_OPENEDX_CONNECTOR_HMAC_SECRET>
AI_CONNECTOR_HMAC_SKEW_SECONDS=300
AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS=edx.cms.fpl.edu.vn,scms.fpl.edu.vn,app.cms.fpl.edu.vn
AI_CONNECTOR_SESSION_BRIDGE_ALLOWED_RETURN_HOSTS=dash-cms.fpl.edu.vn
AI_CONNECTOR_SESSION_BRIDGE_TTL_SECONDS=60
```

`AI_CONNECTOR_SESSION_BRIDGE_SECRET` có thể để trống; CMS connector sẽ fallback sang `AI_CONNECTOR_HMAC_SECRET`. Nếu dùng secret bridge riêng thì AI Server phải đặt cùng giá trị ở `OPENEDX_SESSION_BRIDGE_SECRET`.

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
  "service": "openedx_ai_connector",
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

## Tutor config

`tutor-plugins/openedx_connector.py` là source of truth cho cả việc cài Django connector package và render các `AI_CONNECTOR_*` settings vào LMS/CMS. Không cần plugin phụ `ai_learning_connector_env.py` hoặc `docker-compose.override.yml` riêng.

Production tối thiểu phải đặt HMAC secret giống phía AI Server:

```bash
tutor config save \
  --set AI_CONNECTOR_HMAC_SECRET='<same-value-as-AI-server-OPENEDX_CONNECTOR_HMAC_SECRET>' \
  --set AI_CONNECTOR_SESSION_BRIDGE_ALLOWED_RETURN_HOSTS='dash-cms.fpl.edu.vn' \
  --set AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS='edx.cms.fpl.edu.vn,scms.fpl.edu.vn,app.cms.fpl.edu.vn' \
  --set AI_QUIZ_RUNTIME_ALLOWED_ORIGINS='https://app.cms.fpl.edu.vn,https://edx.cms.fpl.edu.vn,https://scms.fpl.edu.vn,https://dash-cms.fpl.edu.vn'
```

Nếu AI Server dùng `AUTH_MODE=openedx_sso`, hai phía phải có cùng bridge secret. Có thể dùng luôn HMAC secret (mặc định fallback) hoặc đặt cặp riêng:

```text
CMS/Open edX: AI_CONNECTOR_SESSION_BRIDGE_SECRET
AI Server:    OPENEDX_SESSION_BRIDGE_SECRET
```

Không commit giá trị thật của bất kỳ secret nào vào Git.

## Unified Open edX Connector academic endpoints

The connector also exposes API-first Student Management endpoints under the canonical connector namespace for AI Server:

```http
POST /api/ai-connector/v1/users/resolve
POST /api/ai-connector/v1/courses/search
```

Security uses HMAC headers from AI Server:

```text
X-AI-Connector-Timestamp
X-AI-Connector-Signature
X-AI-Connector-Nonce (recommended)
```

The current verifier accepts both the nonce-aware canonical signature and the legacy nonce-less signature for rolling compatibility. New AI Server code should send a unique nonce per request.

Use the same connector secret as AI Server `OPENEDX_CONNECTOR_HMAC_SECRET`:

```env
AI_CONNECTOR_HMAC_SECRET=<same-secret-as-AI-server>
```

`users/resolve` must resolve the canonical CMS/Open edX username used by the deployment (for FPL student flows this is the normalized RollNumber/student code), not fuzzy-match by display name or email.

## Student Progress Dashboard component grades

`POST /api/ai-connector/v1/class-analytics` returns best-effort component/subsection grade breakdown when the Open edX deployment has `PersistentSubsectionGrade` rows for the requested users and course.

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
