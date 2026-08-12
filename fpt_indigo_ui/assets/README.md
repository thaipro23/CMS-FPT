# FPT UI assets

Thư mục này chứa asset runtime được vendor cùng source để build Open edX không phụ thuộc Internet.

Các file bắt buộc:

- `fpt-polytechnic-logo.png`
- `fpt-students.png`
- `fpt-campus-primary.jpg`
- `fpt-campus-secondary.jpg`

Không thay các file này bằng URL remote trong `tutor-plugins/fpt_indigo_ui.py`. Khi cập nhật hình ảnh, thay file trong Git, review trực quan và build/smoke-test lại trên UAT.
