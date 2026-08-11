# FPT Polytechnic UI trên Tutor Indigo

Branch triển khai: `fpt-indigo-ui`

Mục tiêu của patch này là giữ nguyên logic Open edX/Indigo/Paragon và chỉ thêm lớp branding FPT Polytechnic.

## Phạm vi

- Authn MFE: giữ form đăng nhập và FEID; ẩn Register; đổi slogan/màu nền sang FPT.
- LMS Course Discovery: giữ Search, Filters, Course Grid; thêm Hero Slider 3 slide phía trên.
- Learner Dashboard: giữ CourseList mặc định; chèn banner nhỏ phía trên danh sách.
- Header MFE: thay logo Indigo bằng FPT Polytechnic.
- Footer MFE + LMS legacy: bỏ Tutor/Open edX marketing links, dùng thông tin liên hệ FPT.
- Dark mode: tắt toggle và ép light mode.

## Cài plugin

```bash
source ~/tutor-venv/bin/activate
cd /opt/openedx/CMS-FPT

git fetch origin --prune
git switch fpt-indigo-ui
git reset --hard origin/fpt-indigo-ui

cp tutor-plugins/fpt_indigo_branding.py "$(tutor plugins printroot)/fpt_indigo_branding.py"

tutor plugins enable fpt_indigo_branding
tutor config save --set INDIGO_ENABLE_DARK_TOGGLE=false
tutor config save --set INDIGO_FOOTER_NAV_LINKS='[]'
tutor config save
```

## Kiểm tra plugin được load

```bash
tutor plugins list | grep -E 'indigo|fpt_indigo_branding'
python -m py_compile "$(tutor plugins printroot)/fpt_indigo_branding.py"
```

## Build Open edX

Cần build Open edX để nhận Hero Slider và footer LMS legacy:

```bash
tutor images build openedx \
  --build-arg EDX_PLATFORM_REPOSITORY=https://github.com/thaipro23/CMS-FPT.git \
  --build-arg EDX_PLATFORM_VERSION=fpt-indigo-ui
```

## Build MFE

Cần build MFE để nhận Authn branding, FPT header/footer, Learner Dashboard banner và bỏ dark toggle:

```bash
tutor images build mfe
```

Nếu server thiếu RAM khi build MFE, không dùng `--no-cache`; giữ BuildKit cache và tăng swap/builder memory trước khi build lại.

## Start

```bash
tutor local stop
tutor local start -d
```

## Smoke test

1. Login MFE: chỉ có Sign in, không có Register; FEID vẫn đăng nhập được.
2. `/courses`: Hero Slider xuất hiện phía trên Search/Filters; Course Grid vẫn hoạt động như trước.
3. Learner Dashboard: banner mới xuất hiện nhưng danh sách course vẫn là Indigo mặc định.
4. Footer: hiển thị `caodang@fpt.edu.vn` và địa chỉ 13 Phan Tây Nhạc.
5. Header: logo FPT Polytechnic; không còn nút dark mode.
6. Refresh nhiều trang MFE để chắc chắn theme luôn ở light mode.

## Rollback

```bash
source ~/tutor-venv/bin/activate
tutor plugins disable fpt_indigo_branding
tutor config save
tutor local stop
tutor local start -d
```

Nếu đã build image với patch và muốn rollback hoàn toàn giao diện, build lại `openedx` và `mfe` sau khi disable plugin.

## Ghi chú

Patch không thay đổi logic enrollment, course discovery search/filter, course list, login/FEID hoặc grading. Đây là lớp UI/branding đặt trên Indigo để giảm xung đột khi nâng cấp Open edX.
