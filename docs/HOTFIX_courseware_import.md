# Hotfix lỗi `No module named courseware`

Nếu bạn chỉ muốn sửa nhanh bản đang có mà chưa thay cả zip, mở file:

```text
E:\FPL\openedx-platform\openedx_unit_reset\openedx_unit_reset\services.py
```

Tìm dòng:

```python
from courseware.models import StudentModule
```

Thay bằng:

```python
try:
    from lms.djangoapps.courseware.models import StudentModule
except ImportError:
    from courseware.models import StudentModule
```

Sau đó build lại image vì plugin được pip install trong image:

```bat
tutor images build openedx --no-cache
tutor local stop
tutor local start -d
```

Rồi chạy lại migrate/check:

```bat
docker exec -it tutor_local-lms-1 bash -lc "cd /openedx/edx-platform && python manage.py lms migrate openedx_unit_reset --settings=tutor.production"
docker exec -it tutor_local-lms-1 bash -lc "cd /openedx/edx-platform && python manage.py lms openedx_unit_reset_check --settings=tutor.production"
```
