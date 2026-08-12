# FPT Polytechnic UI trên Tutor Indigo

Branch phát triển/UAT: `fpt-indigo-ui`.

Mục tiêu là giữ nguyên logic Open edX, Indigo và Paragon, chỉ thêm lớp branding FPT Polytechnic. UI mới phải được build và smoke-test trên UAT trước khi merge vào branch ổn định.

## Phạm vi

- Authn MFE: giữ form đăng nhập và FEID, ẩn Register, áp dụng màu/hero FPT.
- LMS Course Discovery: giữ Search, Filters và Course Grid; thêm Hero Slider phía trên.
- Learner Dashboard: giữ CourseList mặc định; chỉ thêm banner nhỏ.
- Header/Footer: logo và thông tin FPT Polytechnic.
- Theme: tắt dark-mode toggle và ép light mode.

## Kiến trúc portable

Các Django plugin nghiệp vụ và FPT UI đều đi cùng source repository:

```text
CMS-FPT/
├── openedx_connector_plugin/
├── openedx_unit_reset/
├── fpt_indigo_ui/
│   └── assets/
│       ├── fpt-polytechnic-logo.png
│       ├── fpt-students.png
│       ├── fpt-campus-primary.jpg
│       └── fpt-campus-secondary.jpg
├── tutor-plugins/
│   ├── openedx_connector.py
│   ├── openedx_unit_reset.py
│   └── fpt_indigo_ui.py
└── scripts/
    ├── fpt-ui-setup.sh
    └── fpt-ui-build.sh
```

**Contract:** tất cả asset runtime phải được vendor trong Git. Docker/Tutor không được `curl` logo/banner từ website ngoài trong lúc build.

Tutor expose checkout edx-platform bằng named build context `edx-platform`. `fpt_indigo_ui.py` lấy asset trực tiếp từ context này:

```dockerfile
COPY --from=edx-platform /fpt_indigo_ui/assets/... /openedx/staticfiles/indigo/images/fpt/...
```

Điều này làm build deterministic và không phụ thuộc DNS, URL ảnh hoặc website FPT/third-party.

## Setup máy mới

Kích hoạt Tutor virtualenv rồi chạy từ repository:

```bash
source ~/tutor-venv/bin/activate
cd /opt/openedx/edx-platform
bash scripts/fpt-ui-setup.sh
```

Script setup là idempotent và thực hiện:

1. Kiểm tra đủ 4 asset vendor trong Git.
2. Kiểm tra/thêm source mount edx-platform cho Tutor.
3. Tạo symlink từ `$(tutor plugins printroot)` đến 3 Tutor plugin trong repository.
4. Enable `openedx_connector`, `openedx_unit_reset`, `fpt_indigo_ui`.
5. `py_compile` plugin.
6. `tutor config save`.
7. Xác minh generated Dockerfile có 4 `COPY --from=edx-platform` và không còn remote asset download.

Không dùng `cp` để đồng bộ Tutor plugin và không dùng file bind mount cho plugin Python.

## Build

```bash
bash scripts/fpt-ui-build.sh
```

Script build:

1. Chạy preflight/setup.
2. Build `openedx` bằng BuildKit cache mặc định.
3. Kiểm tra đủ 4 asset trong image Open edX.
4. Build `mfe`.
5. Không tự restart UAT nếu chưa yêu cầu.

Khi đã sẵn sàng restart:

```bash
bash scripts/fpt-ui-build.sh --restart
```

Không dùng `--no-cache` mặc định.

## Asset runtime

Sau build, image phải có:

```text
/openedx/staticfiles/indigo/images/fpt/
├── fpt-polytechnic-logo.png
├── fpt-students.png
├── fpt-campus-primary.jpg
└── fpt-campus-secondary.jpg
```

## Smoke test bắt buộc trước merge

1. Login MFE: form đăng nhập chuẩn và FEID vẫn hoạt động; không có Register; hero FPT hiển thị đúng.
2. `/courses`: Hero Slider nằm trên Search/Filters; Search, Filters, Course Grid hoạt động nguyên bản.
3. Learner Dashboard: banner FPT hiển thị; CourseList Indigo không bị thay đổi logic.
4. Header/Footer: logo, email và địa chỉ FPT đúng.
5. Dark mode: không còn toggle; refresh nhiều trang vẫn giữ light mode.
6. Kiểm tra desktop/tablet/mobile.

Chỉ merge sau khi các bước trên PASS và chủ dự án xác nhận.

## Rollback UI

```bash
tutor plugins disable fpt_indigo_ui
tutor config save
tutor images build openedx
tutor images build mfe
tutor local stop
tutor local start -d
```

`openedx_connector` và `openedx_unit_reset` độc lập với FPT UI, không disable hai plugin này khi chỉ rollback branding.
