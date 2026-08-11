# FPT Polytechnic UI trên Tutor Indigo

Branch triển khai: `fpt-indigo-ui`

Mục tiêu của patch này là giữ nguyên logic Open edX/Indigo/Paragon và chỉ thêm lớp branding FPT Polytechnic.

## Phạm vi

- Authn MFE: giữ form đăng nhập và FEID; ẩn Register; đổi slogan/màu nền sang FPT và dùng ảnh sinh viên thật.
- LMS Course Discovery: giữ Search, Filters, Course Grid; thêm Hero Slider 3 slide dùng ảnh FPT Polytechnic thật.
- Learner Dashboard: giữ CourseList mặc định; chèn banner nhỏ phía trên danh sách.
- Header MFE: thay logo Indigo bằng logo FPT Polytechnic thật.
- Footer MFE + LMS legacy: bỏ Tutor/Open edX marketing links, dùng logo và thông tin liên hệ FPT.
- Dark mode: tắt toggle và ép light mode.

## Cấu trúc Tutor plugin

Các Tutor plugin được đặt riêng, ngang hàng trong thư mục `tutor-plugins/`:

```text
tutor-plugins/
├── openedx_connector.py
├── openedx_unit_reset.py
└── fpt_indigo_ui.py
```

`fpt_indigo_ui.py` chỉ phụ trách UI/branding; không trộn logic connector hoặc Unit Reset.

## Asset thương hiệu

Logo được đối chiếu với bài quy chuẩn sử dụng logo chính thức của FPT Polytechnic năm 2024. Ảnh banner lấy từ các bài viết/hình ảnh do website FPT Polytechnic đăng tải.

Trong lúc build image Open edX, plugin tải asset một lần và lưu local tại:

```text
/openedx/staticfiles/indigo/images/fpt/
├── fpt-polytechnic-logo.png
├── fpt-students.jpg
├── fpt-hanoi-campus.jpg
└── fpt-campus.jpg
```

Frontend production sử dụng bản local này, không hotlink ảnh mỗi lần người dùng mở trang.

## Cài plugin

```bash
source ~/tutor-venv/bin/activate
cd /opt/openedx/CMS-FPT

git fetch origin --prune
git switch fpt-indigo-ui
git reset --hard origin/fpt-indigo-ui

cp tutor-plugins/fpt_indigo_ui.py "$(tutor plugins printroot)/fpt_indigo_ui.py"

tutor plugins enable fpt_indigo_ui
tutor config save --set INDIGO_ENABLE_DARK_TOGGLE=false
tutor config save --set INDIGO_FOOTER_NAV_LINKS='[]'
tutor config save
```

## Kiểm tra plugin được load

```bash
tutor plugins list | grep -E 'indigo|fpt_indigo_ui'
python -m py_compile "$(tutor plugins printroot)/fpt_indigo_ui.py"
```

## Build Open edX

Cần build Open edX trước để nhận asset local, Hero Slider và footer LMS legacy:

```bash
tutor images build openedx \
  --build-arg EDX_PLATFORM_REPOSITORY=https://github.com/thaipro23/CMS-FPT.git \
  --build-arg EDX_PLATFORM_VERSION=fpt-indigo-ui
```

## Build MFE

Sau khi Open edX build thành công:

```bash
tutor images build mfe
```

MFE lấy logo/ảnh login từ LMS qua `/static/indigo/images/fpt/` nên Open edX phải được build trước.

Nếu server thiếu RAM khi build MFE, không dùng `--no-cache`; giữ BuildKit cache và tăng swap/builder memory trước khi build lại.

## Start

```bash
tutor local stop
tutor local start -d
```

## Kiểm tra asset sau build

```bash
docker exec -it tutor_local-lms-1 bash -lc "ls -lh /openedx/staticfiles/indigo/images/fpt"
```

Có thể test trực tiếp:

```bash
curl -kI https://cms-test.poly.edu.vn/static/indigo/images/fpt/fpt-polytechnic-logo.png
curl -kI https://cms-test.poly.edu.vn/static/indigo/images/fpt/fpt-students.jpg
```

Cả hai phải trả về HTTP 200.

## Smoke test

1. Login MFE: chỉ có Sign in, không có Register; FEID vẫn đăng nhập được; bên trái dùng ảnh sinh viên FPT Polytechnic thật.
2. `/courses`: Hero Slider dùng ảnh FPT thật và xuất hiện phía trên Search/Filters; Course Grid vẫn hoạt động như trước.
3. Learner Dashboard: banner mới xuất hiện nhưng danh sách course vẫn là Indigo mặc định.
4. Footer: logo FPT Polytechnic thật, `caodang@fpt.edu.vn` và địa chỉ 13 Phan Tây Nhạc.
5. Header: logo FPT Polytechnic thật; không còn nút dark mode.
6. Refresh nhiều trang MFE để chắc chắn theme luôn ở light mode.

## Rollback

```bash
source ~/tutor-venv/bin/activate
tutor plugins disable fpt_indigo_ui
tutor config save
tutor local stop
tutor local start -d
```

Nếu đã build image với patch và muốn rollback hoàn toàn giao diện, build lại `openedx` và `mfe` sau khi disable plugin.

## Ghi chú

Patch không thay đổi logic enrollment, course discovery search/filter, course list, login/FEID hoặc grading. Đây là lớp UI/branding đặt trên Indigo để giảm xung đột khi nâng cấp Open edX.
