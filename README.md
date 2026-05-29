# openedx_unit_reset_secure_full_v5

Bản này là zip đầy đủ cho plugin `openedx_unit_reset`, dựa trên patch bạn gửi và sửa lại các lỗi/rủi ro sau:

- Sửa lỗi build `AttributeError: module 'openedx_unit_reset.settings.common' has no attribute 'plugin_settings'`.
- Sửa lỗi runtime/migrate trên Tutor Ulmo: `ModuleNotFoundError: No module named 'courseware'` bằng import chuẩn `lms.djangoapps.courseware.models`.
- Giữ endpoint theo patch cũ: `/api/unit-reset/v1/status/` và `/api/unit-reset/v1/reset/`.
- Reset chỉ cho `request.user`, không nhận `user_id` từ client.
- `POST` reset có CSRF.
- Kiểm tra user đã login, user có enrollment trong course, và Unit thuộc course.
- Cooldown lấy max từ các field kiểu `submission_wait_seconds`/`time_between_attempts`, fallback 600 giây.
- Cooldown dùng `last_reset_at` và cố gắng dùng `StudentModule.modified` như mốc submit gần nhất.
- Dùng `transaction.atomic()` + `select_for_update()` để chống bấm nhiều tab vượt cooldown.
- Có bảng `UnitResetControl` và `UnitResetAudit`.
- Migration đổi `unit_usage_key` từ 512 về 255 để giảm rủi ro lỗi index length trên MySQL/InnoDB.

## Cấu trúc

```text
openedx_unit_reset/
  setup.py
  openedx_unit_reset/
    __init__.py
    apps.py
    admin.py
    models.py
    services.py
    urls.py
    views.py
    settings/
      __init__.py
      common.py
      production.py
      devstack.py
    migrations/
      __init__.py
      0001_initial.py
    management/commands/openedx_unit_reset_check.py

tutor-plugins/
  openedx_unit_reset.py

frontend-snippets/
  ResetUnitButton.jsx

docs/
  INSTALL_LOCAL_WINDOWS.md
  template_patch_snippet.html
```

## Cài trên máy Windows local

Xem chi tiết: `docs/INSTALL_LOCAL_WINDOWS.md`.

Tóm tắt:

```bat
cd /d E:\FPL\openedx-platform
for /f "delims=" %i in ('tutor plugins printroot') do copy /Y "E:\FPL\openedx-platform\tutor-plugins\openedx_unit_reset.py" "%i\openedx_unit_reset.py"
tutor plugins enable openedx_unit_reset
tutor config save
tutor images build openedx --no-cache
tutor local stop
tutor local start -d
docker exec -it tutor_local-lms-1 bash -lc "cd /openedx/edx-platform && python manage.py lms migrate openedx_unit_reset --settings=tutor.production"
docker exec -it tutor_local-lms-1 bash -lc "cd /openedx/edx-platform && python manage.py lms openedx_unit_reset_check --settings=tutor.production"
```

## API

Status:

```http
GET /api/unit-reset/v1/status/?course_id=<COURSE_ID>&unit_usage_key=<UNIT_USAGE_KEY>
```

Reset:

```http
POST /api/unit-reset/v1/reset/
Content-Type: application/json
X-CSRFToken: <csrftoken>

{
  "course_id": "course-v1:FPT+DBI102+SU26",
  "unit_usage_key": "block-v1:FPT+DBI102+SU26+type@vertical+block@quiz_1"
}
```

Không gửi `user_id`.

## Lưu ý UI

Backend plugin đã đầy đủ để build/migrate/test API. Nút “Làm lại bài” vẫn cần gắn vào đúng Learning MFE hoặc template Unit bạn đang dùng.

Có sẵn mẫu ở:

```text
frontend-snippets/ResetUnitButton.jsx
docs/template_patch_snippet.html
```

Nếu bạn gửi source/zip MFE Learning đang dùng, có thể gắn nút vào đúng vị trí Unit page.

## Lưu ý chống gian lận

Plugin này giới hạn tốc độ reset bằng cooldown, nhưng không thể chống dò 100% nếu ngân hàng câu hỏi quá ít. Nên cấu hình OLX:

```text
max_attempts = 1
submission_wait_seconds = 600 hoặc 1800
showanswer = never hoặc past_due
show_reset_button = false
```

Với random 15 câu/lần, mỗi bank difficulty nên có ít nhất 80-150 câu nếu muốn giảm khả năng dò qua nhiều lần reset.
