# FPT UI assets

Các asset runtime của lớp FPT Polytechnic UI được vendor cùng source để build Open edX/Tutor hoàn toàn offline, không hotlink và không tải ảnh từ Internet trong Docker build.

## Bộ asset production

- `fpt-polytechnic-logo.png` — logo header/login/footer; 360×122.
- `fpt-students.png` — sinh viên FPT Polytechnic áo cam; 1360×906; dùng làm visual chính ở Course Discovery.
- `fpt-campus-primary.jpg` — sinh viên học/thực hành với thiết bị quay; 1920×1655; dùng cho Login background và supporting card.
- `fpt-campus-secondary.jpg` — không gian thực hành F&B; 900×600; dùng cho Learner Dashboard banner và supporting card.
- `manifest.json` — nguồn, vai trò, kích thước, dung lượng và SHA-256 của ba ảnh nội dung.

## Nguồn và provenance

Ba ảnh nội dung production đều được lấy từ các bài/asset trên website chính thức `caodang.fpt.edu.vn` rồi tối ưu và commit vào Git:

1. `fpt-students.png`: bài tuyển sinh/tân sinh viên 2026 của FPT Polytechnic; source gốc WebP 1360×906.
2. `fpt-campus-primary.jpg`: bài học thực hành livestream tại phòng lab FPT Polytechnic; source gốc 1920×1655.
3. `fpt-campus-secondary.jpg`: bài giới thiệu hệ thống phòng thực hành ngành Du lịch – Nhà hàng – Khách sạn; source gốc 900×600.

Logo hiện tại là logo FPT Polytechnic chuẩn, bản PNG 360×122. Các quy tắc sử dụng logo được đối chiếu với bài “Quy chuẩn sử dụng logo trường Cao Đẳng FPT Polytechnic” trên website chính thức.

URL chính xác của từng ảnh nội dung và hash binary nằm trong `manifest.json`; không sao chép URL đó vào runtime UI.

## Quy trình refresh ảnh

`python scripts/fpt-ui-refresh-assets.py` là **maintainer tool**, không chạy trong Tutor/Open edX Docker build. Script:

- chỉ dùng danh sách nguồn FPT Polytechnic đã curate;
- kiểm tra kích thước và tỷ lệ ảnh;
- bỏ nguồn không đạt thay vì ép crop ảnh kém phù hợp;
- tối ưu output;
- ghi lại `manifest.json` và SHA-256.

Workflow `.github/workflows/fpt-ui-refresh-assets.yml` chạy tool này trên GitHub runner, tạo contact sheet để review và chỉ commit khi cả ba ảnh cùng đạt yêu cầu.

## Quality gate

`scripts/fpt-ui-validate-assets.py` dùng Python standard library để kiểm tra ngay trong CI:

- logo tối thiểu 300×100;
- student visual tối thiểu 1000×600;
- Login/primary visual tối thiểu 1200×650;
- supporting visual tối thiểu 900×550;
- kích thước, byte size và SHA-256 phải khớp `manifest.json`;
- nguồn trong manifest phải thuộc `caodang.fpt.edu.vn`.

## Quy tắc production

1. Asset phải nằm trong Git trước khi build.
2. Runtime/Tutor Docker build không được tải ảnh từ Internet.
3. Không thay asset bằng URL remote trong `tutor-plugins/fpt_indigo_ui.py` hoặc patch runtime.
4. Sau khi thay ảnh phải để CI asset/static PASS rồi mới build UAT.
5. Không merge `fpt-indigo-ui` vào stable trước khi UAT PASS và có xác nhận rõ ràng.
