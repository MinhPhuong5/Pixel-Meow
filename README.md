# Pixel Meow Legion v1.7 · TikTok LIVE Auto Battle

Game tự chạy hai phe trên giao diện dọc 9:16 tại [127.0.0.1:8080](http://127.0.0.1:8080/). Bản chơi thủ công ở [/game](http://127.0.0.1:8080/game). Bản sao nguyên gốc nằm trong `original/index.html`.

## Cài đặt Windows

Cần Python 3.12. Mở PowerShell trong thư mục dự án và cài dependencies một lần.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Chạy `START-MOCK.bat`. Trình duyệt tự mở game ở chế độ setup âm thanh. Nếu thấy nút **Bật âm thanh cho nguồn phát**, bấm một lần. Game bắt đầu ngay khi trình duyệt hoặc nguồn OBS mở trang. Terminal nhận các lệnh sau. Dừng bằng Ctrl+C.

```text
chat alice !meow
like alice 50
chat bob !monster
like bob 100
gift rose 10
streak rose 4
spawn cat basic 5
spawn cat mecha 1
spawn enemy slime 5
spawn enemy boar 1
cannon cat
cannon enemy
finish cat
finish enemy
change-map
status
restart
connection connecting
connection connected
connection disconnected
connection mock
drop-ws
```

`change-map` đi qua mapping quà mô phỏng. `streak` tạo bản tin đang streak, bản tin kết thúc và bản sao để kiểm tra chống trùng. `drop-ws` ngắt WebSocket của game để kiểm tra tự nối lại. `status` và [/health](http://127.0.0.1:8080/health) báo hàng đợi, kết quả, ACK và FPS nếu có. [/catalog](http://127.0.0.1:8080/catalog) hiển thị dữ liệu quà backend đã nhận.

## Chuẩn bị phiên TikTok

1. Sao chép `.env.example` thành `.env` và điền `TIKTOK_USERNAME` bằng tên tài khoản dùng để phát. Giữ mật khẩu và session token ngoài dự án.
2. Chạy `START-LIVE.bat`. Mở trang qua nguồn hình chính. Backend tự thử kết nối khi tài khoản lên sóng và tự kết nối lại sau khi mất kết nối. TikTokLive 7.0.1 dùng dịch vụ ký cộng đồng miễn phí theo quota của thư viện. [Tài liệu TikTokLive](https://github.com/isaackogan/TikTokLive/blob/master/README.md)
3. Dùng một phiên LIVE thử để lấy ID thật từ `/catalog` hoặc log gift. Mỗi dòng trong `config/gifts.json` có một hành động mẫu. Điền `gift_id` và giữ `enabled: true` cho các quà muốn kích hoạt. Mapping được đọc lại khi lưu. ID đang bật phải duy nhất. ID LIVE mặc định là `null`.
4. Dựa vào trường `diamonds` thực tế để chọn mức `common`, `mid` hoặc `high`. Với đổi bản đồ, chọn một quà `mid` có giá trị cao hơn đáng kể so với quà phổ thông trong danh mục của phiên. Giá trị quà do TikTok cung cấp. `quantity` là số quân trên mỗi đơn vị quà, phần trăm hồi căn cứ hoặc số giây buff. `cooldown` tính theo giây trận. `priority` lớn hơn gửi sớm hơn, riêng đổi map luôn FIFO.
5. Kiểm tra terminal có ACK cuối và tên/icon đúng trên panel. Tên quà lấy từ catalog hoặc bản tin TikTok, có thể đặt `display_label` ngắn. Quà chưa có metadata sẽ xuất hiện sau khi catalog hoặc bản tin cung cấp dữ liệu. Khi không lấy được catalog, game dùng cache riêng theo username và đánh dấu chưa xác minh. Lỗi tải icon hiện ký hiệu ◆.

Giữ **một trang** nguồn hình trên đúng địa chỉ 127.0.0.1:8080. Trang đầu giữ quyền thực thi quà. Trang khác hiện trạng thái nguồn phụ và có thể tiếp quản sau khi nguồn chính mất kết nối. Một lần ngắt WebSocket trên cùng trang giữ nguyên trận và các yêu cầu đổi map. Tải lại hoặc đóng trang tạo trận mới. Kết quả thắng/hòa lưu theo hồ sơ trình duyệt và địa chỉ nguồn hình.

## Quy tắc trận và xử lý tải

- Hai căn cứ bắt đầu với 1.800 HP. AI dùng đội hình định sẵn và tăng nhịp theo số trận. Đợt tấn công luân phiên theo đồng hồ, độc lập với quà. Quân từ quà dùng thống kê gốc và giới hạn quân riêng cho AI chừa chỗ cho quà.
- Trận kết thúc khi căn cứ bị phá. Hai căn cứ bị phá trong cùng bước mô phỏng thì hòa. Trong LIVE, mỗi phe có thể bắn tầm xa vào căn cứ đối phương khi giữ được tuyến trước bằng một quân tầm xa. Cơ chế này giúp trận tiếp tục có sát thương lên căn cứ khi hai đội hình kẹt ở giữa.
- Sau màn kết quả 5 giây, map xoay Green Meadow → Sunset Canyon → Shadow Forest → Green Meadow. Quà đổi map vẫn đặt trước map cho trận kế tiếp khi trận đang diễn ra. Tối đa 3 lượt đổi map chờ, mỗi trận mới xử lý một lượt theo thứ tự xác nhận. Game trả ACK từ chối cho tác động trực tiếp vào trận đã kết thúc.
- Người xem bình luận `!meow` hoặc `1` để chọn Mèo, `!monster` hoặc `2` để chọn Quái. Gửi riêng lệnh, có thể viết hoa hoặc viết thường. Có thể đổi phe sau 10 giây kể từ lần chọn gần nhất. Phe được ghi nhớ theo người xem và phòng LIVE khi mất kết nối tạm thời. Tim từ người chưa chọn phe không tạo tăng tốc. Game dùng tốc độ tim trung bình khoảng 5 giây gần nhất của mỗi phe. Đủ 20 tim/giây thì nhịp triệu hồi tự động tăng gấp đôi. Giới hạn số quân vẫn áp dụng. Tim chỉ tác động đến nhịp triệu hồi, còn tốc độ đánh, tốc độ di chuyển và lượng sát thương của quân giữ nguyên.
- Pháo mỗi phe hồi 28 giây riêng. Một phát cho mỗi lượt quà đã chốt. Quà pháo trong thời gian hồi có ACK từ chối và thông báo. Tùy chọn hồi căn cứ và buff +25% sát thương có sẵn trong mapping, tắt mặc định.
- Quà streak chỉ thực hiện khi có bản tin kết thúc. Backend chống trùng theo group hoặc ID bản tin khi thư viện cung cấp trường nhận diện. Game nhận tối đa 64 hành động chờ từ backend và 24 lô triệu hồi trong trận. Mỗi lô tối đa 16 quân, xử lý từng quân theo nhịp 120 ms. Lô không đủ chỗ có thể chờ đến 12 giây rồi trả ACK phần đã thực hiện hoặc từ chối. Mỗi phe tối đa 24 quân và một quân hạng nặng cùng lúc. Game giới hạn 96 projectile, 72 hiệu ứng PixelFX chiến đấu và 400 hạt.
- Các lượt hết hạn, hàng đợi đầy và thao tác bị giới hạn được ghi trong terminal. Nếu trình duyệt cũ đã mất trước khi xác nhận, backend ghi lượt chưa xác nhận và tránh áp dụng lại vào phiên trình duyệt khác.

## Quà kết liễu

Trong `config/gifts.json`, `finish_battle_cat` hỗ trợ Mèo, `finish_battle_enemy` hỗ trợ Quái. Dùng hai `gift_id` khác nhau từ catalog TikTok, với `tier: "high"`. Chọn quà có diamond cao hơn đáng kể so với quà triệu hồi. Hai ID mặc định để trống đến khi xác minh từ catalog. Tên, icon và diamond trên panel lấy từ dữ liệu quà. Mock dùng `finish cat`, `finish enemy` hoặc `streak finish-enemy 3`.

Lượt kết liễu hợp lệ đầu tiên của trận khóa kết quả. Căn cứ đối phương bị phá sau hiệu ứng 3,6 giây. `/health` lưu 50 kết quả gần nhất trong tiến trình backend, có người gửi nếu kết thúc bằng quà. Quà đến sau khi khóa được phản hồi với lý do từ chối nếu tác động trực tiếp vào trận. Quà đổi bản đồ vẫn xếp hàng cho trận kế. Lượt kết liễu đã gắn với một trận sẽ hết hiệu lực khi trận đó kết thúc.

## Icon quà TikTok

Game lấy catalog qua TikTokLive 7.0.1. Nếu chưa có phiên LIVE, danh mục công khai và cache giúp xem trước quà. Khi kết nối thật, backend kiểm tra lại catalog của phòng. Xem `/catalog` để biết ID, giá, trạng thái mapping và kết quả đồng bộ icon.

Các quà cấu hình bằng `gift_name` và `coin_price` chỉ được gán ID khi tên xuất hiện duy nhất và giá khớp. Hai mục có cùng `price_pair` phải cùng giá và đều hợp lệ trước khi kích hoạt. Nếu danh mục báo đổi giá, thiếu quà hoặc trùng tên, xem cảnh báo hoặc mục `mapping` ở `/catalog`. `live_label` chỉ rút ngắn tên trên màn hình. Trường `diamond_count` dùng làm mệnh giá quà khi payload thiếu trường giá xu riêng.

Icon đã xác thực nằm trong `assets/gifts/`. Cache `data/gift_catalog_cache.json` dùng chung cho game và Dashboard. Khi nguồn ảnh đổi, bản cũ vẫn hiển thị nếu tải lại lỗi. Nếu chưa có ảnh, giao diện hiển thị tên quà. Ảnh được tải ở backend với giới hạn 2 MiB và 1.024 pixel mỗi chiều. Dashboard đọc tệp nội bộ trên cổng 8081.

## Dashboard thống kê

- Game và nguồn hình OBS ở `http://127.0.0.1:8080/`. Dashboard dành cho người vận hành ở `http://127.0.0.1:8081/`. Cả hai chỉ lắng nghe trên localhost và dùng chung một kết nối TikTok.
- `START-LIVE.bat` mở game khi cả hai listener sẵn sàng. Đặt `OPEN_DASHBOARD=true` trong `.env` để mở thêm Dashboard. `START-DASHBOARD.bat` chỉ mở trang quản trị. `START-MOCK.bat` khởi động cả hai listener để thử bằng lệnh terminal.
- Chọn phiên hiện tại, hôm nay, toàn bộ lịch sử hoặc một phiên/ngày cụ thể. Dashboard dùng múi giờ UTC+7. Bộ lọc phe dựa trên phe người xem đã chọn trong từng phiên. Số quà hỗ trợ phe và BXH hỗ trợ dựa trên phe của hành động được ghép. Số lần dùng kỹ năng tính theo xác nhận thực thi của game.
- History tính một sự kiện cho mỗi streak đã chốt và đếm đầy đủ số đơn vị quà trong streak. Diamonds ước tính chỉ tính từ giá trị đã biết. Quà chưa có giá trị vẫn được đếm và hiển thị riêng. Ba nút CSV xuất toàn bộ kết quả theo bộ lọc cùng BOM UTF-8 và xử lý tiền tố công thức Excel.
- Dữ liệu lưu vào `data/analytics-live.sqlite3` hoặc `data/analytics-mock.sqlite3`. Mock và LIVE dùng hai tệp riêng. Đóng Dashboard vẫn ghi dữ liệu. Hành động chưa có xác nhận khi khởi động lại được ghi thất bại với lý do ở lịch sử. Backend dùng ID TikTok để chống ghi trùng khi có ID sự kiện hoặc ID combo. Các sự kiện thiếu định danh chỉ có thể được đếm theo từng bản tin nhận được.
- Thử nhiều người gửi bằng `gift rose 3 alice "Cùng tên"` và `streak goo 5 bob "Cùng tên"`. Dữ liệu diamond mẫu chỉ có trong Mock. Một số quà mẫu cố ý để trống diamond để kiểm tra cách hiển thị.

## Âm thanh tự động

Âm thanh bật mặc định. Mỗi nguồn game dùng một AudioContext. AI và quà dùng cùng tiếng triệu hồi của đơn vị. Tiếng đánh chỉ phát khi gây sát thương, có tiếng riêng cho đòn tầm xa, đòn mạnh, boss, căn cứ và quân bị hạ. Game có nhạc hiệu hai phe, âm thanh chuyển trận và chuỗi âm thanh riêng cho kết liễu. Trong hiệu ứng kết liễu, âm lượng chiến đấu và triệu hồi giảm rồi khôi phục ở trận kế. Mỗi hiệu ứng có giới hạn tần suất, với tối đa 16 voice và bộ nén đầu ra.

- Mở nguồn game với `?audio=setup`. Nếu browser đang chặn autoplay, bấm **Bật âm thanh cho nguồn phát** một lần. Nút biến mất khi AudioContext chạy.
- URL game thông thường giữ khung hình sạch. Để bật âm thanh từ URL đó, đặt focus vào đúng nguồn và nhấn **A**. Mỗi tab hoặc lần tải trang tạo một tài liệu riêng. Kích hoạt ở tab khác không cấp quyền cho nguồn OBS. Terminal và `/health` báo trạng thái AudioContext.
- Chỉnh `GAME_VOLUME_MASTER` và các mục `GAME_VOLUME_COMBAT`, `GAME_VOLUME_SPAWN`, `GAME_VOLUME_SKILL`, `GAME_VOLUME_BATTLE`, `GAME_VOLUME_GIFT`, `GAME_VOLUME_AMBIENT` trong `.env` rồi khởi động lại backend. Giá trị từ 0 đến 1. `GAME_AUDIO=false` tắt âm thanh. Kênh ambient sẵn cho nhạc nền, hiện chỉ có SFX tổng hợp.
- Hai pháo kiểm tra mục tiêu từ giây 24, sau đó mỗi 5 giây. Chỉ bắn khi sẵn sàng và có quân địch trong tầm. Pháo dùng chung cooldown hiện có với quà, kể cả khi tắt âm thanh. `GAME_AUTO_CANNON=false` tắt lịch bắn tự động. Quà pháo vẫn chịu cooldown, nên có thể đến khi pháo vừa bắn tự động.

## OBS Studio

1. Đặt Base Canvas và Output Resolution thành **1080 × 1920**. Thêm **Browser Source** với URL `http://127.0.0.1:8080/?audio=setup`, Width 1080, Height 1920. Có thể bật Custom Frame Rate 60. Tắt tùy chọn tắt nguồn khi ẩn và làm mới khi chuyển scene. [OBS Browser Source](https://obsproject.com/kb/Browser-Source)
2. Nếu có **Control audio via OBS**, bật tùy chọn này. Mở **Interact** trên chính Browser Source và bấm nút bật âm thanh khi cần. Bạn cũng có thể dùng URL sạch `http://127.0.0.1:8080/` và nhấn **A** trong Interact. Kiểm tra thanh tín hiệu của nguồn trong **Audio Mixer**. [Điều khiển âm thanh Browser Source](https://obsproject.com/forum/threads/is-there-a-way-to-record-a-tab-but-not-hear-the-tab-while-its-recording.162123/)
3. Nếu Browser Source không phát tiếng, mở game trong cửa sổ Edge riêng, kích hoạt âm thanh tại đó, rồi dùng **Window Capture** kèm **Capture Audio** hoặc **Application Audio Capture** cho Edge. Chọn một đường thu cho game để tránh thu trùng với Desktop Audio. Đóng tab game xem thử nếu đang dùng Browser Source, vì tab đầu tiên có thể giữ quyền nhận quà. [OBS Application Audio Capture](https://obsproject.com/kb/application-audio-capture-guide)
4. Trước LIVE, chạy Mock với cùng nguồn OBS, ghi thử hai phút. Để game tự chiến đấu, rồi thử `gift crown 1` và `finish cat`. Xem lại bản ghi để kiểm tra tiếng va chạm, pháo, chuỗi kết liễu, nhạc hiệu và độ rõ của tiếng nền chiến đấu.

## TikTok LIVE Studio

Chọn bố cục Portrait 1080 × 1920. Nếu tài khoản và phiên bản LIVE Studio có nguồn Link/Browser cho localhost, dùng URL trên làm nguồn chính và xem preview để kiểm tra khung.

Nếu cần Window Capture, mở Edge bằng `msedge.exe --app=http://127.0.0.1:8080/`. Capture cửa sổ app đó rồi crop viền và đặt vừa canvas 9:16. Giữ cửa sổ chạy trong khi phát. Nếu dùng OBS Virtual Camera và LIVE Studio chấp nhận nguồn đó, chỉ để một Browser Source trong OBS và kiểm tra đầu vào hình/âm thanh ở LIVE Studio. Dùng quyền phát trên LIVE Studio của tài khoản. Stream key OBS cần quyền riêng nếu chọn phát trực tiếp từ OBS. [Điều kiện phát TikTok LIVE](https://support.tiktok.com/en/log-in-troubleshoot/troubleshooting/issues-with-tiktok-live)

## Kiểm thử

Với Microsoft Edge và Playwright, dừng server hiện tại rồi chạy bộ kiểm thử từ thư mục dự án.

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -X utf8 tests/smoke.py
.\.venv\Scripts\python.exe -X utf8 tests/analytics_smoke.py
.\.venv\Scripts\python.exe -X utf8 tests/audio_smoke.py
```

Đã chạy bộ Mock đầu cuối trên Microsoft Edge ở 1080 × 1920 và 390 × 844. Đo riêng ở 1080 × 1920 trên Windows, Edge báo 60 FPS ở tải thường và trong cửa sổ đo sau khi tạo 24 quân mỗi phe, bắn đồng thời hai pháo. Kết quả là số đo trên máy thử, phụ thuộc cấu hình nguồn phát.

Phần nhận bình luận, tim, quà TikTok và catalog của phòng thật và OBS/TikTok LIVE Studio cần kiểm thử trên tài khoản và máy dùng để phát. Kiểm tra quyền phát thương mại trong `LICENSE_AUDIT.md`.
