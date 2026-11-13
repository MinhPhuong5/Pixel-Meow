# Kiểm tra giấy phép Pixel Meow Legion v1.7

Ngày kiểm tra 13/11/2026. Phạm vi gồm file HTML nhận được, trang phát hành của game và thư viện được nhúng hoặc khai báo trực tiếp.

| Thành phần | Bằng chứng và phạm vi quyền đã xác định |
| --- | --- |
| Mã game, bản đồ, gameplay, Cat Paradise | Workspace ban đầu chỉ có `index.html`. Trang phát hành của Tinyforge Game Studio cho tải game và ghi credit thư viện. Chưa tìm được giấy phép cấp quyền sửa hoặc phân phối mã game. [Trang phát hành](https://tinyforge-game-studio.itch.io/pixel-meow-legion) |
| Sprite mèo/quái, căn cứ, cảnh nền, hiệu ứng riêng, âm thanh | Nằm trong HTML gốc. Phần credit PixelFX xác định các hiệu ứng `meow/*` là nội dung riêng của game. Chưa tìm được giấy phép riêng cho các asset này hoặc quyền khai thác thương mại. [Trang phát hành](https://tinyforge-game-studio.itch.io/pixel-meow-legion) |
| PixelFX 0.3.0 và các hiệu ứng Sprite Fusion được ghi credit | Khối `PIXELFX:BEGIN` chứa MIT và thông báo Copyright Patch/Hugo Duprez. Bản sửa giữ nguyên thông báo. Phạm vi áp dụng cho phần thư viện đã ghi credit. [PixelFX](https://github.com/patch-gg/pixelfx) · [Sprite Fusion Pixel Art VFX](https://www.spritefusion.com/pixel-art-vfx) |
| Three.js r170 | Khối `PARK3D:BEGIN` có thông báo Copyright 2010–2024 Three.js Authors và SPDX MIT. Giữ nguyên thông báo trong HTML. MIT yêu cầu giữ notice khi phân phối phần thư viện. [Three.js MIT](https://github.com/mrdoob/three.js/blob/r170/LICENSE) |
| TikTokLive 7.0.1 | Modified AGPL-3.0. Phần 18 có ngoại lệ tích hợp kể cả game LIVE tùy các điều kiện phần 19–21. Dự án cài qua pip và dùng API client. [Giấy phép TikTokLive](https://github.com/isaackogan/TikTokLive/blob/master/LICENSE) |
| FastAPI, uvicorn, python-dotenv | Dependencies cài qua pip. FastAPI dùng MIT, uvicorn và python-dotenv dùng BSD-3-Clause. [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) · [uvicorn](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) · [python-dotenv](https://github.com/theskumar/python-dotenv/blob/main/LICENSE) |
| Tên và icon quà | Đọc từ dữ liệu phiên TikTok, dùng URL từ TikTok khi có và cache metadata trên localhost. Việc TikTok cung cấp metadata chưa xác lập quyền sử dụng các hình ảnh đó ngoài phạm vi điều khoản của nền tảng. [TikTok Virtual Items Policy](https://t.tiktok.com/legal/page/row/virtual-items/en) |

**Phát LIVE thương mại.** Quyền tải và chơi game trên itch.io, quyền chỉnh sửa/phân phối source, và quyền phát phiên bản đã chỉnh sửa có nhận quà là các quyền cần kiểm tra riêng. Chưa xác minh được quyền thương mại đối với game gốc và asset. Hãy xin xác nhận của Tinyforge Game Studio hoặc kiểm tra thỏa thuận đã có trước khi phát thương mại hoặc phân phối bản sửa. Điều kiện phát và kiếm tiền của TikTok áp dụng riêng theo tài khoản và khu vực. [TikTok LIVE](https://support.tiktok.com/en/live-gifts-wallet/tiktok-live/what-is-tiktok-live) · [TikTok Virtual Items Policy](https://t.tiktok.com/legal/page/row/virtual-items/en)

Bản sao gốc `original/index.html` có SHA-256 `13872636129bc807aa5597a7fafb3a347f8f24eea007d082a2a4895ade477c4c`.
