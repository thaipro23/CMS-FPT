# FPT Polytechnic UI trên Tutor Indigo

Branch phát triển/UAT: `fpt-indigo-ui`.

Mục tiêu là giữ nguyên nghiệp vụ Open edX, FEID, Unit Reset, Indigo và Paragon; lớp FPT chỉ thay branding/UX. Mọi thay đổi phải qua static/fixture validation, build verification và UAT smoke trước khi merge vào branch ổn định.

## Baseline đã kiểm chứng

- Tutor: `21.x` (UAT hiện tại: 21.0.6).
- Open edX: `OPENEDX_COMMON_VERSION=release/ulmo.3`.
- Authn: tag `release/ulmo.3`, dùng DefaultLayout; `ENABLE_IMAGE_LAYOUT=False`.
- Custom Learning MFE: `/opt/openedx/frontend-app-learning`, branch `mfe-unit-reset`.
- Unit Reset backend: package `openedx-unit-reset` từ `CMS-FPT/openedx_unit_reset`.

Script setup sẽ fail-fast nếu baseline khác, source tracked bị sửa chưa commit, custom Learning mất marker Unit Reset hoặc Learning không được Tutor map vào build context `mfe -> learning-src`.

Chỉ dùng các override sau cho compatibility test có chủ đích, không dùng trong UAT/production bình thường:

```bash
FPT_UI_ALLOW_UNTESTED_BASELINE=1
FPT_UI_SKIP_LEARNING_GUARD=1
FPT_LEARNING_REPO=/duong/dan/frontend-app-learning
FPT_LEARNING_BRANCH=mfe-unit-reset
```

## Phạm vi UI

- Authn MFE: giữ form đăng nhập/FEID/forgot-password; ẩn Register; áp dụng layout FPT responsive.
- LMS Course Discovery: giữ Search, Filters và Course Grid; thêm Hero Slider phía trên.
- Learner Dashboard: giữ CourseList mặc định; thêm banner FPT.
- Header/Footer: logo và thông tin FPT Polytechnic.
- Branded shell: không hiển thị dark-mode toggle.
- Unit Reset: không được thay đổi hoặc mất frontend/backend khi rebuild UI.

## Kiến trúc portable

```text
CMS-FPT/
├── openedx_connector_plugin/
├── openedx_unit_reset/
├── fpt_indigo_ui/
│   ├── assets/
│   │   ├── fpt-polytechnic-logo.png
│   │   ├── fpt-students.png
│   │   ├── fpt-campus-primary.jpg
│   │   └── fpt-campus-secondary.jpg
│   └── patches/
│       ├── authn.patch
│       ├── openedx.patch
│       └── runtime.patch
├── tutor-plugins/
│   ├── openedx_connector.py
│   ├── openedx_unit_reset.py
│   └── fpt_indigo_ui.py
└── scripts/
    ├── fpt-ui-validate-static.sh
    ├── fpt-ui-setup.sh
    ├── fpt-ui-build.sh
    └── fpt-ui-smoke.sh

/opt/openedx/frontend-app-learning/
└── src/courseware/course/sequence/unit-reset/UnitResetButton.jsx
```

**Contract:** asset FPT phải được vendor trong Git. Docker/Tutor không được tải logo/banner từ website ngoài trong lúc build.

Tutor expose checkout edx-platform bằng named build context `edx-platform`. Asset được copy trực tiếp vào image:

```dockerfile
COPY --from=edx-platform /fpt_indigo_ui/assets/... /openedx/staticfiles/indigo/images/fpt/...
```

Custom Learning MFE được Tutor-MFE nhận diện qua basename `frontend-app-learning` và map vào production build context `learning-src`. Đây là điều kiện bắt buộc để rebuild shared MFE image không làm mất Unit Reset.

## Quy trình chuẩn trên UAT

Kích hoạt Tutor virtualenv, checkout đúng branch và bảo đảm cả hai repository không có tracked local change:

```bash
source ~/tutor-venv/bin/activate

cd /opt/openedx/edx-platform
git fetch origin
git checkout fpt-indigo-ui
git pull --ff-only origin fpt-indigo-ui
git status

cd /opt/openedx/frontend-app-learning
git status
git branch --show-current

cd /opt/openedx/edx-platform
bash scripts/fpt-ui-build.sh --restart
```

`fpt-ui-build.sh --restart` là entrypoint chuẩn. Không cần chạy setup/validator/smoke riêng trước đó vì script tự nối toàn pipeline.

## Pipeline tự động

Pipeline hiện thực hiện theo thứ tự:

1. Kiểm tra Git/Tutor/Docker và Docker daemon.
2. Chạy static/fixture validator.
3. Kiểm tra Tutor 21.x + `release/ulmo.3` + clean tracked source.
4. Kiểm tra custom Learning branch `mfe-unit-reset`, UnitResetButton marker và clean tracked source.
5. Tự thêm mount edx-platform/Learning nếu thiếu.
6. Enable/link `openedx_connector`, `openedx_unit_reset`, `fpt_indigo_ui`.
7. `tutor config save` và kiểm tra generated LMS settings + MFE `env.config.jsx`.
8. Xác nhận Learning mount thật sự map vào `mfe -> learning-src`.
9. Ghi checkpoint image ID Open edX + MFE hiện tại để rollback.
10. Build Open edX image.
11. Kiểm tra asset, Hero, Footer, Header và import/version package `openedx-unit-reset` bên trong image.
12. Build shared MFE image.
13. Copy compiled `authn`, `learner-dashboard`, `learning` ra kiểm tra marker FPT và Unit Reset.
14. Restart Tutor khi có `--restart`.
15. Chờ LMS ready, kiểm tra asset/route LMS và trực tiếp `MFE_HOST/authn/`, `MFE_HOST/learner-dashboard/`.
16. Chỉ commit transaction khi toàn bộ bước PASS.

Nếu một bước build/verify/restart/smoke thất bại và hai image cũ tồn tại, script tự restore tag về image ID trước build. Nếu deployment đã bị restart, script cố gắng khởi động lại deployment với image cũ.

Không dùng `--no-cache` mặc định.

## CI

GitHub Actions `FPT UI Static Validation` chạy trên mọi push vào `fpt-indigo-ui` và pull request liên quan. CI chạy:

- ShellCheck các script deployment.
- Bash syntax + Python compile.
- Authn Node patch syntax.
- Apply Authn patch hai lần để kiểm tra idempotence.
- Kiểm tra single-wedge/shared-edge responsive geometry.
- Apply Open edX patch hai lần để kiểm tra idempotence.
- Guard Tutor/Ulmo/Authn layout/rendered-config/Learning mount/Unit Reset artifact contract.

CI xanh không thay thế full Tutor/Docker build trên UAT; nó chặn phần lớn lỗi source trước khi server phải build.

## Smoke/UAT bắt buộc trước merge

- Login desktop/tablet/mobile: branding đúng, không overflow; FEID và forgot-password hoạt động; không Register.
- `/courses`: Hero nằm trên Search/Filters; CTA cuộn tới `#discovery-form`; Search/Filters/Course Grid giữ nguyên.
- Learner Dashboard: banner FPT hiển thị và CourseList/enrollment behavior không đổi.
- Learning: Course/Progress/Instructor/Unit/Quiz render bình thường.
- Unit Reset: timer/cooldown/reset/iframe reload vẫn hoạt động theo contract hiện tại.
- Header/Footer/logo/asset không 404.
- Authn/Learner Dashboard MFE host truy cập được qua Caddy/DNS.

Chỉ merge sau khi UAT PASS và chủ dự án xác nhận.

## Rollback thủ công dự phòng

Transactional build tự rollback image tag khi có checkpoint hợp lệ. Chỉ dùng rollback thủ công khi cần tắt riêng branding FPT:

```bash
tutor plugins disable fpt_indigo_ui
tutor config save
tutor images build openedx
tutor images build mfe
tutor local stop
tutor local start -d
```

Không disable `openedx_connector` hoặc `openedx_unit_reset` khi chỉ rollback branding.
