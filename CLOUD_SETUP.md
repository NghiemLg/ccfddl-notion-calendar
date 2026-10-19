# CCFDDL -> Notion + Google Calendar, chạy miễn phí qua GitHub Actions

Phiên bản này chỉ cần giao diện web để cấu hình. Không cần bật laptop/server hoặc chạy Python local. Python >= 3.10; GitHub runner chạy Python 3.12.

## A. Dữ liệu đầu vào và đầu ra

- Input: CCFDDL `https://ccfddl.github.io/conference/allconf.yml` (file YAML `.yml`).
- Output: Notion Database (các trang dữ liệu qua Notion API), Google Calendar (sự kiện có giờ, nhắc trước 28/7/1 ngày qua Google Calendar API). Không tạo `.csv` hoặc `.ics`.
- Lọc: hội nghị `rank.core` thuộc `A` / `A*` và chủ đề AI hoặc Robotics. Các hội nghị robot có thể cần bổ sung whitelist (`ROBOT_NAMES`) trong `sync.py` nếu không được nguồn đánh nhãn đúng.
- Không tự xóa những deadline đã qua hoặc không còn trong nguồn; không tự sửa `Progress` cá nhân do bạn nhập.

## B. Chuẩn bị Notion qua trình duyệt

1. Tạo Database `AI Robotics Deadlines` với đúng các thuộc tính: `Name` = Title, `Source ID` = Text, `Deadline` = Date, `CORE` = Text, `CCF` = Text, `Field` = Select, `Kind` = Select, `URL` = URL, `Progress` = Select.
2. Tạo Integration tại `https://www.notion.so/profile/integrations`, cấp Read / Insert / Update content; thêm integration vào database ở Connections.
3. Sao chép **Notion integration token** và **Database ID** để thêm vào GitHub Secrets. Database cần có đúng một data source tương thích Notion API version `2025-09-03`.

## C. Chuẩn bị Google Calendar + Service Account 100% web

1. Mở `https://calendar.google.com` -> Other calendars (+) -> Create new calendar. Đặt tên `CCFDDL AI Robotics` và chỉnh timezone `Asia/Ho_Chi_Minh`. Sao chép **Calendar ID** trong Settings -> Integrate calendar (thường kết thúc bằng `@group.calendar.google.com`).
2. Mở `https://console.cloud.google.com` -> tạo Project -> APIs & Services -> Library -> bật **Google Calendar API**.
3. Trong Google Cloud Console -> IAM & Admin -> Service Accounts -> Create service account (ví dụ `ccfddl-sync`).
4. Chọn service account vừa tạo -> **Keys** -> Add key -> Create new key -> JSON. Trình duyệt tải về JSON có `client_email`, `private_key`.
5. Quay lại Google Calendar -> Settings -> lịch `CCFDDL AI Robotics` -> Share with specific people or groups -> thêm **client_email** từ JSON, cấp quyền `Make changes to events` (cho sửa sự kiện; không cần quyền quản lý chia sẻ). Nếu tài khoản tổ chức chặn chia sẻ ngoài miền, quản trị viên cần cấp quyền hoặc phải dùng OAuth.
6. Mở JSON trong trình soạn thảo và sao chép **toàn bộ nội dung** vào GitHub Secret `GOOGLE_SERVICE_ACCOUNT_JSON`. Không commit key JSON vào repository, không dán lên issue hoặc chat công khai.

Không cần tạo OAuth Desktop credentials hay `token.json` khi dùng service account. Calendar vẫn do tài khoản Google của bạn sở hữu; service account chỉ là người được cấp quyền sửa sự kiện.

## D. Tạo repository GitHub bằng trình duyệt

1. Vào `https://github.com/new`, tạo **Private repository** tên `ccfddl-notion-calendar` (nên chọn private cho dễ quản lý). `main` là nhánh mặc định.
2. Giải nén file `.zip` từ gói này (có thể dùng trình quản lý file, không cần cài Python); upload các file `sync.py`, `requirements.txt`, `tests/`, `.github/workflows/sync.yml` bằng Add file -> Upload files. Nếu giao diện upload không giữ được thư mục `.github`, trong web chọn **Add file -> Create new file**, đặt chính xác tên `.github/workflows/sync.yml`, dán nội dung từ bản zip. Đừng chỉ upload file `.zip` làm một file duy nhất.
3. Repo -> Settings -> Secrets and variables -> Actions -> New repository secret, tạo:
   - `NOTION_TOKEN`: Integration token
   - `NOTION_DATABASE_ID`: Database ID
   - `GOOGLE_CALENDAR_ID`: Calendar ID
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: toàn bộ JSON service account
4. Không cần tạo secret `GOOGLE_TOKEN_JSON` nếu dùng service account. Secret này chỉ là đường dự phòng cho ai đã có OAuth refresh token của ứng dụng đã được xác thực.
5. Repo -> Actions -> `CCFDDL to Notion and Google Calendar` -> Run workflow -> để `dry_run=true` lần đầu. Xem log để kiểm tra các deadline.
6. Khi danh sách đúng, chạy `Run workflow` lần nữa với `dry_run=false` để ghi Notion/Calendar.
7. Kể từ đó schedule chạy hằng ngày lúc **06:17 Asia/Ho_Chi_Minh**; GitHub có thể chạy trễ trong thời gian tải cao.

## E. Tránh lỗi và lưu ý

- `FileNotFoundError credentials.json`: bản cloud đã hỗ trợ `GOOGLE_SERVICE_ACCOUNT_JSON`; kiểm tra secret đã thêm và định dạng JSON còn nguyên.
- `403 requiredAccessLevel`: phải cấp quyền `Make changes to events` cho đúng `client_email`, đúng Calendar ID và service account đang dùng.
- `403 service account keys disabled`: chính sách Google Cloud organization hạn chế tạo JSON key; có thể dùng OAuth có refresh token, hoặc Google Apps Script.
- `401 invalid_grant`: thường xuất hiện với `GOOGLE_TOKEN_JSON` hết hiệu lực, không xảy ra do refresh token nếu dùng service account.
- `404 notion`: Integration chưa kết nối đúng database hoặc nhập nhầm database ID.
- `Notion database needs properties/types`: tên cột hoặc kiểu dữ liệu không đúng.
- `No upcoming matches`: kiểm tra nguồn CCFDDL/YAML hiện tại và bộ lọc.
- GitHub `schedule` chạy trên default branch; private repo có hạn mức free minutes tùy gói; public repo có thể bị GitHub vô hiệu schedule sau 60 ngày không có hoạt động.
- Không dùng quyền Actions `contents: write`; workflow chỉ cần `contents: read`.
- Đừng đặt thông tin cá nhân hoặc token vào code / README / file `.env` commit lên GitHub.

## F. Notion Calendar

Kết nối cả Google Calendar và Notion workspace tại `https://calendar.notion.so`. Nếu bật hiển thị cả nguồn Notion Database và nguồn Google Calendar, một deadline có thể xuất hiện hai lần; ẩn một nguồn nếu muốn.

## G. Kiểm thử khi có Python (tùy chọn, không bắt buộc)

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python sync.py --source tests/fixture.yml --dry-run
```
