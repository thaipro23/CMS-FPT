# FPT UI assets

Các asset runtime của lớp FPT Polytechnic UI được vendor cùng source để build Open edX/Tutor hoàn toàn offline, không hotlink và không tải ảnh từ Internet trong Docker build.

## File runtime bắt buộc

- `fpt-polytechnic-logo.png`
- `fpt-students.png`
- `fpt-campus-primary.jpg`
- `fpt-campus-secondary.jpg`

## Nguồn asset V8

- `fpt-polytechnic-logo.png`: FPT Polytechnic logo, Wikimedia Commons (`File:FPT Polytechnic.png`, CC BY-SA 4.0). Bản runtime giữ nguyên nội dung logo, chỉ tối ưu PNG lossless.
- `fpt-students.png`: hình sinh viên FPT Polytechnic học cùng laptop, nguồn Chúng Ta/FPT; chuyển JPEG → PNG để đúng build contract hiện tại.
- `fpt-campus-primary.jpg`: hình cơ sở FPT Polytechnic từ `caodang.fpt.edu.vn`, tối ưu JPEG progressive cho web.
- `fpt-campus-secondary.jpg`: hình nhận diện/campus FPT Polytechnic từ bài Poly NextGen trên Báo Nhân Dân, tối ưu JPEG progressive cho web.

Các URL nguồn chỉ dùng để truy xuất asset khi chuẩn bị source. Runtime/build **không** phụ thuộc các URL này.

## Quy tắc

1. Asset phải nằm trong Git trước khi build.
2. Không thay bằng URL remote trong `tutor-plugins/fpt_indigo_ui.py`.
3. Sau khi thay ảnh phải chạy `scripts/fpt-ui-setup.sh`, build cần thiết và smoke-test UAT.
4. Không merge `fpt-indigo-ui` vào stable trước khi UAT PASS và có xác nhận rõ ràng.
