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

## Endpoint publish thật

```http
POST /api/ai-connector/v1/courses/<course_id>/libraries
POST /api/ai-connector/v1/libraries/<library_key>/problems
POST /api/ai-connector/v1/courses/<course_id>/problems
```

Các endpoint publish dùng Content Libraries V2 Python API trong CMS/Studio:

- `create_library`
- `get_library`
- `create_library_block`
- `set_library_block_olx`
- `add_library_block_static_asset_file`
- `publish_component_changes`
- `publish_changes`

ACMS `25.9.16.7.2.64.16.5.7.2.18` có thể gửi tối đa 4 ảnh cho một câu hỏi. Connector `0.1.9` kiểm tra MIME/base64/SHA-256, upload ảnh vào static asset của chính Library component, thay placeholder `__ACMS_MEDIA_<id>__` bằng URL Open edX thật rồi mới lưu/publish OLX. Connector từ chối publish nếu placeholder media chưa được resolve, để tránh câu hỏi được publish nhưng ảnh bị hỏng.

Nếu Open edX đang chạy chưa có Content Libraries V2 API hoặc chưa xác định được Studio staff user để publish, plugin trả lỗi rõ ràng và **không báo thành công giả**.

## Bảo mật publish / HMAC

Production không cho anonymous publish và không fallback sang "first staff user". AI Server ký request server-to-server bằng cùng secret với Open edX connector:

```env
AI_CONNECTOR_PUBLISH_USERNAME=<studio_staff_or_admin_username>
AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH=false
AI_CONNECTOR_HMAC_SECRET=<same_secret_as_AI_Server_OPENEDX_CONNECTOR_HMAC_SECRET>
AI_CONNECTOR_HMAC_SKEW_SECONDS=300
AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS=cms.fpl.edu.vn,scms.fpl.edu.vn,app.cms.fpl.edu.vn
AI_CONNECTOR_MAX_BODY_BYTES=25165824
```

`AI_CONNECTOR_MAX_BODY_BYTES` mặc định là 24 MiB để đủ cho OLX + metadata + base64 media của ACMS v18; request vượt giới hạn trả `413` thay vì parse không giới hạn.

Các endpoint publish/rollback/diagnostics là HMAC-only. Browser staff cookie không được dùng thay HMAC tại các endpoint `csrf_exempt` này.

Asset/transcript download có SSRF guard: chỉ download từ Studio host hiện tại hoặc host nằm trong `AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS`; redirect bị chặn và cookie chỉ forward cho cùng host.

## Kiểm tra plugin

```bash
curl https://scms.fpl.edu.vn/api/ai-connector/v1/health
```

Kỳ vọng connector package sau khi build/install:

```json
{
  "status": "ok",
  "service": "openedx_ai_connector",
  "version": "0.1.9",
  "publish_implementation": "content_libraries_v2_python_api",
  "stub_publish": false
}
```

## Content Library dùng chung

ACMS hiện dùng một canonical Content Library organization cho ngân hàng câu hỏi:

```env
AI_CONNECTOR_LIBRARY_ORG=FPT
AI_CONNECTOR_AUTO_CREATE_ORG=false
```

`FPT` ở đây là Organization sở hữu Content Library dùng chung, **không phải** org của mọi delivery course. Course vật lý vẫn giữ full Course ID/org thực tế, ví dụ `course-v1:FPL+...`, `course-v1:FPS+...`.

Production phải tạo sẵn Organization `FPT`; connector mặc định fail closed nếu organization này chưa tồn tại và không tự tạo khi `AI_CONNECTOR_AUTO_CREATE_ORG=false`.

## Tutor config plugin mode

Canonical Tutor helper nằm tại:

```text
tutor-plugins/openedx_connector.py
```

Plugin này chịu trách nhiệm cả hai phần:

1. Cài `openedx_connector_plugin` vào Open edX image.
2. Render `AI_CONNECTOR_*` settings vào **cả LMS và CMS**.

Ví dụ:

```bash
cp tutor-plugins/openedx_connector.py "$(tutor plugins printroot)/openedx_connector.py"
tutor plugins enable openedx_connector

tutor config save \
  --set AI_CONNECTOR_LIBRARY_ORG=FPT \
  --set AI_CONNECTOR_AUTO_CREATE_ORG=false \
  --set AI_CONNECTOR_MAX_BATCH_SIZE=5000 \
  --set AI_CONNECTOR_MAX_BODY_BYTES=25165824
```

Secrets như `AI_CONNECTOR_HMAC_SECRET` và `AI_CONNECTOR_PUBLISH_USERNAME` phải được cấp riêng cho môi trường triển khai, không commit vào Git.

## Unified Open edX Connector academic endpoints

Connector expose các API Student Management dưới canonical namespace:

```http
POST /api/ai-connector/v1/users/resolve
POST /api/ai-connector/v1/courses/search
POST /api/ai-connector/v1/class-analytics
POST /api/ai-connector/v1/course-enrollment/batch
POST /api/ai-connector/v1/course-enrollment/enroll
POST /api/ai-connector/v1/course-enrollment/remove
```

Security dùng HMAC headers từ AI Server:

```text
X-AI-Connector-Timestamp
X-AI-Connector-Nonce
X-AI-Connector-Signature
```

Dùng cùng connector secret với AI Server `OPENEDX_CONNECTOR_HMAC_SECRET`:

```env
AI_CONNECTOR_HMAC_SECRET=<same-secret-as-AI-server>
AI_CONNECTOR_MAX_BATCH_SIZE=5000
```

Với sinh viên, payload chuẩn dùng `RollNumber`/`student_code` làm canonical Open edX username. Lookup không phân biệt hoa thường nhưng khi tạo mới phải giữ đúng RollNumber; AP username không phải fallback cho student. Teacher/legacy payload giữ contract riêng của connector.

`AI_CONNECTOR_MAX_BATCH_SIZE` có thể được cấp qua process env hoặc Tutor-rendered Django settings; process env được ưu tiên khi cả hai cùng tồn tại.

## Student Progress Dashboard component grades

`POST /api/ai-connector/v1/class-analytics` trả best-effort component/subsection grade breakdown khi Open edX deployment có `PersistentSubsectionGrade` cho users/course tương ứng.

Response có thể gồm:

```json
{
  "grade_percent": 78.5,
  "component_scores": [
    {"key":"...", "name":"Quiz 1", "earned":8.0, "possible":10.0, "percent":80.0, "category":"subsection"}
  ],
  "grade": {"percent": 78.5, "components": []}
}
```

Nếu component grades không khả dụng, endpoint vẫn trả user/enrollment/progress/course-grade data và để `component_scores` rỗng.

## Lưu ý release Open edX

- Open edX có Content Libraries V2: plugin có thể tạo Library, import Problem thật và upload component static assets.
- Nếu Content Libraries V2 Python API không khả dụng: connector fail rõ ràng thay vì giả lập publish thành công.
- Canonical API prefix là `/api/ai-connector/v1/`; `/api/ai-student-insight/v1/` chỉ còn alias tương thích rolling upgrade.
