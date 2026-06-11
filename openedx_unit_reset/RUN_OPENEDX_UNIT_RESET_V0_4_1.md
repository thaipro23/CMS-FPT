# openedx_unit_reset v0.4.1 - Complete Custom Timed Practice Quiz Runtime

## Có gì mới

- Learning MFE hiển thị đồng hồ đếm ngược qua repo `thaipro23/frontend-app-learning`, branch `mfe-unit-reset`.
- Plugin lưu quiz session thật ở backend: `started_at`, `expires_at`, `status`, `reset_available_at`.
- Hết giờ: MFE gọi timeout, gửi message auto-submit, sau đó gọi lock.
- Runtime JS được inject vào LMS-rendered problem frames để tự submit các câu đã tích và khóa input.
- Server-side middleware chặn submit muộn sau khi session hết giờ.
- Nút Làm lại bài luôn hiện. Nếu chưa đủ cooldown, backend báo còn phải chờ bao lâu.

## Cài plugin

Copy thư mục `openedx_unit_reset` vào plugin repo Open edX của bạn, ví dụ:

```bash
cp -r openedx_unit_reset /opt/openedx/CMS-FPT/openedx_unit_reset
```

Sau đó migrate và restart:

```bash
tutor local run lms ./manage.py lms migrate openedx_unit_reset
tutor local restart lms lms-worker cms cms-worker
```

Check API:

```bash
curl -I http://cms-test.poly.edu.vn/api/unit-reset/v1/quiz-session/runtime.js
```

## Build Learning MFE

```bash
cd /opt/openedx/frontend-app-learning

git fetch origin
git checkout mfe-unit-reset
git reset --hard origin/mfe-unit-reset

# Nếu server không có npm, build bằng Docker:
docker run --rm \
  -v "$PWD":/app \
  -w /app \
  node:20-bullseye \
  bash -lc "npm ci && npm run build"

sudo chown -R $(id -u):$(id -g) /opt/openedx/frontend-app-learning

docker cp dist/. tutor_local-mfe-1:/openedx/dist/learning/
tutor local restart mfe
```

## Test end-to-end

1. Tạo Quiz từ AI Server với timer bật.
2. Vào Learning MFE bằng tài khoản student.
3. Mở đúng Unit `Quiz`.
4. Kiểm tra có đồng hồ đếm ngược.
5. Chọn vài đáp án, chờ hết giờ.
6. Kiểm tra hệ thống tự nộp câu đã chọn và khóa lượt làm.
7. Bấm Làm lại bài khi chưa đủ cooldown: phải báo còn chờ bao lâu.
8. Sau cooldown, bấm Làm lại bài: reset Unit, random lại câu và tạo session mới.
