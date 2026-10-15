# Tra cứu repository mà không đọc lại toàn bộ source

`AGENTS.md` là điểm vào ngắn cho AI. `CODE_INDEX.md` định tuyến mã nguồn;
`docs/INDEX.md` định tuyến tài liệu; `docs/CURRENT_STATE.md` ghi snapshot có nguồn.
Chỉ đọc các mục phục vụ tác vụ hiện tại. Không nạp toàn bộ master/history/catalog.

## Bắt đầu một phiên

Chạy từ root repository, với Python 3.9+ và Git:

```sh
git status --short
python scripts/repo_index.py check
python scripts/repo_index.py ls
python scripts/repo_index.py find "teacher report" --limit 10
python scripts/repo_index.py find "grade" --docs --limit 10
```

`find` tìm các từ trong **đường dẫn**, không tìm nội dung/hàm. Kết quả kèm blob SHA
và mode. Sau khi chọn file, đọc đoạn cần thiết hoặc dùng `rg` trong thư mục đã chọn.
`ls <directory>` trả các thư mục/file con trực tiếp, ưu tiên thư mục, cùng số file
phù hợp bộ lọc trong thư mục con. Ở root nó ưu tiên focus, ẩn lịch sử; `ls --all`
hiện cả core/lịch sử. Khi truyền thư mục cụ thể, focus không giới hạn kết quả.
Mặc định tối đa 20 kết quả, `--limit` cho phép 1–100; `total`/`truncated` cho biết
có kết quả bị cắt. `--prefix` giới hạn một thư mục và bỏ bộ lọc focus để tra core.

```sh
python scripts/repo_index.py ls backend/app/services/academic
python scripts/repo_index.py find "report" --prefix backend/app/services/academic
python scripts/repo_index.py find "quiz" --docs --history --limit 10
python scripts/repo_index.py find "runtime" --all --limit 10
```

Ví dụ thư mục phụ thuộc repository; dùng `ls` hoặc `CODE_INDEX.md` để chọn đúng.
`--history` bao gồm lịch sử/thiết kế trong focus; `--all` mở cả core và lịch sử.
Các proposal/spec/plan và tài liệu phiên bản không phải bằng chứng đã triển khai.

## Độ mới và thay đổi cục bộ

Catalog được sinh từ **committed HEAD**, chứa tên file, SHA và mode; không đọc nội
dung file, không gọi mạng, không import ứng dụng và không theo symlink. Toàn bộ file
Git (kể cả core, binary và gitlink) có trong catalog. Không có symbol index hoặc
khẳng định đã kiểm tra nội dung mọi file. Dữ liệu máy đọc nằm tại
`docs/_index/catalog.json`; đừng đưa cả JSON vào prompt.

`check` so metadata trong catalog với Git tree hiện tại rồi liệt kê đường dẫn
modified/added/removed và thay đổi chưa commit (kể cả untracked không bị ignore).
Nó không quét lại nội dung toàn source. Exit code: `0` khớp và sạch; `1` catalog
cũ hoặc working tree có thay đổi; `2` thiếu Git/catalog hoặc dữ liệu không hợp lệ.
`fresh: true` chỉ nói catalog khớp **committed** tree; luôn xem thêm `dirty.total`.

`source_commit` là commit được index, có thể là parent của commit chỉ thêm catalog.
Độ mới dựa trên SHA/mode/tên file, không dựa vào việc `source_commit == HEAD`.
Catalog tự loại chính nó để tránh vòng lặp tự làm cũ. Khi stale, so các file đổi
với scope tác vụ rồi rebuild metadata; không đọc lại toàn bộ tài liệu lịch sử.

```sh
git diff --name-status <source_commit> HEAD -- <thu-muc-lien-quan>
git log -5 --oneline -- <file-lien-quan>
rg -n 'ten_ham|ma_loi' <thu-muc-lien-quan>
```

`find`/`ls` đọc catalog đã lưu và dùng được trong ZIP không có `.git`; chúng không
tự khẳng định độ mới. Nếu không chạy được `check`, ghi rõ giới hạn này. Kiểm tra
Git không xác minh trạng thái production và không làm snapshot nghiệp vụ tự mới.

## Cập nhật sau khi sửa source hoặc di chuyển file

1. Commit các thay đổi dự kiến bằng quy trình Git hiện có. Không stage secret/local data.
2. Chạy `python scripts/repo_index.py build` từ root. Lệnh từ chối source chưa commit.
3. Commit riêng `docs/_index/catalog.json`; chạy lại `check` sau commit.
4. Nếu đổi route/module/ràng buộc, cập nhật `CODE_INDEX.md`, `docs/INDEX.md` hoặc
   `docs/CURRENT_STATE.md` tương ứng trước bước build. Snapshot phải có SHA/nguồn,
   tách kiểm thử đã ghi trong nguồn với kiểm thử vừa chạy, và tách source với deploy.

```sh
python -m unittest discover -s scripts/tests -p 'test_repo_index.py'
python scripts/repo_index.py build
git add docs/_index/catalog.json
git commit -m "docs: refresh repository path index"
python scripts/repo_index.py check
```

Không chạy rebuild/deploy ứng dụng chỉ để tạo index. Không sửa test phiên bản cũ
để làm suite xanh. Những lệnh build/migrate/reset trong tài liệu lịch sử cần đối
chiếu source, môi trường và phạm vi tác vụ trước khi dùng.

## Quy tắc giữ tài liệu gọn

- Sửa tài liệu hiện hành tại đường dẫn ổn định thay vì tạo thêm master/handoff theo phiên bản.
- Mỗi quyết định có một nơi chuẩn; tài liệu khác dùng link.
- Lịch sử giữ để giải thích thay đổi; chỉ đọc khi đúng triệu chứng hoặc mốc Git.
- Không di chuyển hàng loạt release/runbook đang có consumer; tra bằng index và
  giữ đường dẫn cũ. Hai master/README cũ đã có bản lưu tại `docs/history/`.
- Không lưu password, token, private key, cookie hoặc dữ liệu đăng nhập trong docs/index.
