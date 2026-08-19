# Open edX FPT Auth

Plugin đăng nhập dành cho Open edX FPT Polytechnic. Plugin chỉ liên kết danh
tính bên ngoài với `auth_user` đã tồn tại; plugin không tạo, đổi tên hoặc cập
nhật user.

## Identity contract

- FEID (`feid`): lấy `RollNumber` đầu tiên có giá trị trong
  `projectCampuses`, tìm duy nhất một user bằng `username__iexact`.
- Google (`google-oauth2`): yêu cầu `email_verified=true`, tìm duy nhất một
  user bằng `email__iexact`.
- Không FEID email fallback, không Google username fallback.
- User không tồn tại, trùng kết quả hoặc inactive đều bị từ chối.
- Sau khi map, dữ liệu provider bị loại khỏi phần đồng bộ profile phía sau;
  plugin không thay đổi email, username, tên hoặc profile của `auth_user`.
- Khi map thành công, pipeline chuẩn của `python-social-auth` chỉ tạo
  `UserSocialAuth(provider, uid)`; các lần sau dùng liên kết này để đăng nhập.
- FEID dùng `sub` (fallback tương thích `id`) làm UID. Google được cấu hình dùng
  `sub` thay vì email làm UID.

## Provider configuration

Tạo hai `OAuth2ProviderConfig` trong Django Admin, không commit secret vào Git:

| Provider | Backend name | Callback production |
| --- | --- | --- |
| FEID | `feid` | `https://cms.fpl.edu.vn/auth/complete/feid/` |
| Google | `google-oauth2` | `https://cms.fpl.edu.vn/auth/complete/google-oauth2/` |

Provider phải enabled, visible và thuộc đúng Django Site. Các tùy chọn bỏ qua
form đăng ký không làm plugin tạo user: unmatched login luôn bị chặn trước bước
`create_user`.

## Tutor installation

Sao chép Tutor helper vào plugin root; helper sẽ cài package trước
`collectstatic`:

```bash
cp tutor-plugins/fpt_auth.py "$(tutor plugins printroot)/fpt_auth.py"
tutor plugins enable fpt_auth
tutor config save
tutor images build openedx
```

Không chạy build/deploy production trước khi test callback thật trên UAT.

## Verification

Trong LMS container:

```bash
python manage.py lms fpt_auth_check
```

Command chỉ in trạng thái/count, không in email, RollNumber, token hoặc secret.
Command cũng fail nếu có backend/pipeline sai thứ tự, provider trùng hoặc thiếu,
liên kết tới user inactive, UID lỗi, hoặc dữ liệu username/email trùng khi so
sánh không phân biệt hoa thường.

Chạy test trong môi trường Open edX:

```bash
pytest openedx_fpt_auth/openedx_fpt_auth/tests
```

## UAT acceptance matrix

Ghi lại `auth_user` count trước và sau từng ca. Count phải không đổi trong mọi
trường hợp; chỉ `social_auth_usersocialauth` được thêm khi map lần đầu.

| Ca | Kết quả bắt buộc |
| --- | --- |
| FEID, `RollNumber` khớp đúng một active username | Login thành công, tạo link `feid` |
| FEID thiếu/sai/trùng `RollNumber` | Từ chối, không tạo user/link |
| Google, email verified khớp đúng một active email | Login thành công, tạo link `google-oauth2` |
| Google email chưa verified/không có/trùng | Từ chối, không tạo user/link |
| User inactive | Từ chối dù mới map hay đã có social link |
| Login lần hai cùng provider UID | Dùng social link cũ, không map hoặc tạo link lại |
| Callback có `state` sai hoặc thiếu PKCE verifier | Từ chối trước token/user mapping |

Không coi triển khai đạt cho tới khi cả FEID và Google callback thật chạy qua
ma trận này trên UAT với đúng client, secret/public-client mode, redirect URI và
Django Site của production.
