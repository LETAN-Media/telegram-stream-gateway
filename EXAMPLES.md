# Hướng Dẫn Sử Dụng Universal Telegram Media Gateway Cho Mọi Website & Tool

`telegram-stream-gateway` là một hạ tầng Media Gateway đa mục tiêu (Universal Media Infrastructure). Bạn có thể kết nối **bất kỳ website, ứng dụng di động, bot Telegram, hoặc tool nội bộ** nào vào Telegram để lưu trữ và phát trực tuyến (streaming) Video, Audio/Podcast, Hình ảnh và File tài liệu dung lượng lớn (lên tới 2GB - 4GB) mà không tốn chi phí lưu trữ S3/R2 hay băng thông máy chủ.

---

## 1. Các Cách Xác Thực (Authentication)

Gateway hỗ trợ 2 chế độ gọi:

### Cách A: Dùng API Key (Dành cho Backend, Tool, Bot, Script)
Chỉ cần truyền Header hoặc Query Parameter:
- **Header**: `X-API-Key: YOUR_STREAM_SIGNING_SECRET`
- **Hoặc Header**: `Authorization: Bearer YOUR_STREAM_SIGNING_SECRET`
- **Hoặc Query**: `?api_key=YOUR_STREAM_SIGNING_SECRET`

*Khi truyền API Key, bạn có toàn quyền truy cập mọi channel/chat mà tài khoản Telegram của Gateway đã tham gia, không cần tạo chữ ký HMAC từng file.*

### Cách B: Signed URL với HMAC (Dành cho Trình duyệt / HTML5 Player công khai)
Để không lộ Secret Key trên trình duyệt của người xem, Backend của bạn tạo một URL có thời hạn (TTL) chứa `exp` và `sig`:
```text
https://gateway.domain.com/v1/stream/-1001234567890/45?exp=1793889192&sig=abcdef123456...
```
Người xem chỉ có thể xem file trong khoảng thời gian `exp`, không thể giả mạo sang file khác.

---

## 2. Các API Endpoints Có Sẵn

| Phương thức | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `GET` | `/healthz` | Kiểm tra trạng thái hoạt động & kết nối Telegram |
| `GET` | `/v1/chats` | Liệt kê tất cả các nhóm / kênh Telegram đang có kèm ID |
| `GET` | `/v1/feed/{chat_id}` | Lấy danh sách video, audio, ảnh, hoặc file từ kênh (hỗ trợ phân trang) |
| `GET` | `/v1/info/{chat_id}/{message_id}` | Lấy thông tin chi tiết của 1 file (kích thước, tên file, mime, caption) |
| `GET` / `HEAD` | `/v1/stream/{chat_id}/{message_id}` | Phát video/audio streaming với HTTP Range (206 Partial Content) |
| `GET` / `HEAD` | `/v1/file/{chat_id}/{message_id}` | Tải trực tiếp file về máy (`Content-Disposition: attachment`) |
| `GET` | `/v1/thumb/{chat_id}/{message_id}` | Lấy ảnh đại diện (thumbnail / poster) của video / ảnh |

### Tham số hỗ trợ cho `/v1/feed/{chat_id}`:
- `limit`: Số lượng tin nhắn muốn lấy (1 đến 200, mặc định 100).
- `offset_id`: ID tin nhắn để phân trang (lấy các tin nhắn cũ hơn ID này).
- `type`: Lọc loại media:
  - `video`: Chỉ lấy video (mặc định)
  - `audio`: Chỉ lấy nhạc / podcast / ghi âm
  - `photo`: Chỉ lấy hình ảnh
  - `document`: Chỉ lấy tài liệu / file nén
  - `all`: Lấy tất cả media

---

## 3. Ví Dụ 1: Tạo Website Phim / Video Mới (HTML5 + JS)

Chỉ cần tạo file `index.html` đơn giản và nhúng SDK:

```html
<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <title>Trang Xem Phim Mới</title>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
  <style>
    body { font-family: sans-serif; background: #121212; color: #fff; padding: 20px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .card { background: #1e1e1e; border-radius: 8px; overflow: hidden; cursor: pointer; }
    .card img { width: 100%; height: 280px; object-fit: cover; }
    .card h3 { font-size: 14px; margin: 8px; }
    video { width: 100%; max-width: 800px; display: block; margin: 20px auto; border-radius: 8px; }
  </style>
</head>
<body>
  <h1>Danh Sách Video</h1>
  
  <video id="player" controls playsinline></video>
  <div id="movie-grid" class="grid"></div>

  <!-- Nhúng SDK JavaScript -->
  <script src="./sdk/telegram-gateway.js"></script>
  <script>
    const GATEWAY_URL = "https://p01--telegram-stream-gateway--r4v2pzg2klvq.code.run";
    const CHANNEL_ID = -1003952062608; // Điền ID channel của bạn
    const API_KEY = "YOUR_STREAM_SIGNING_SECRET";

    const gw = new TelegramGateway({ gatewayUrl: GATEWAY_URL, secret: API_KEY });
    const player = document.getElementById("player");

    async function loadMovies() {
      const items = await gw.getFeed(CHANNEL_ID, { limit: 20, type: "video" });
      const grid = document.getElementById("movie-grid");
      
      items.forEach(async (m) => {
        const card = document.createElement("div");
        card.className = "card";
        const thumbUrl = gw.getThumbUrl(CHANNEL_ID, m.mid);
        card.innerHTML = `
          <img src="${thumbUrl}" alt="${m.title}" loading="lazy">
          <h3>${m.title}</h3>
        `;
        card.onclick = async () => {
          player.src = await gw.getStreamUrl(CHANNEL_ID, m.mid);
          player.play();
          window.scrollTo({ top: 0, behavior: 'smooth' });
        };
        grid.appendChild(card);
      });
    }

    loadMovies();
  </script>
</body>
</html>
```

---

## 4. Ví Dụ 2: Dùng Trong Node.js / Express / Cloudflare Worker

```javascript
const TelegramGateway = require("./sdk/telegram-gateway.js");

const gw = new TelegramGateway({
  gatewayUrl: process.env.GATEWAY_URL,
  secret: process.env.STREAM_SIGNING_SECRET,
});

// 1. Lấy danh sách tất cả các channel
app.get("/api/my-channels", async (req, res) => {
  const chats = await gw.listChats();
  res.json({ chats });
});

// 2. Lấy danh sách bài hát / podcast từ kênh Audio
app.get("/api/podcasts", async (req, res) => {
  const AUDIO_CHANNEL_ID = -1001987654321;
  const items = await gw.getFeed(AUDIO_CHANNEL_ID, { type: "audio" });
  res.json({ items });
});

// 3. Tạo link nghe nhạc trực tuyến an toàn (hết hạn sau 6 tiếng)
app.get("/api/listen/:mid", async (req, res) => {
  const streamUrl = await gw.getStreamUrl(-1001987654321, req.params.mid, 21600);
  res.redirect(streamUrl);
});
```

---

## 5. Ví Dụ 3: Dùng Trong Python (Script, Bot, Scraper, Fast API)

```python
from sdk.telegram_gateway import TelegramGateway

gw = TelegramGateway(
    gateway_url="https://p01--telegram-stream-gateway--r4v2pzg2klvq.code.run",
    secret="YOUR_STREAM_SIGNING_SECRET"
)

# 1. Liệt kê các kênh để xem ID
chats = gw.list_chats()
for chat in chats:
    print(f"Kênh: {chat['title']} | ID: {chat['id']}")

# 2. Lấy 50 video mới nhất
videos = gw.get_feed(chat_id=-1003952062608, limit=50, media_type="video")
for v in videos:
    print(f"[{v['mid']}] {v['title']} ({v['size'] / (1024*1024):.1f} MB)")

# 3. Tải 1 file dung lượng lớn về máy mà không tốn RAM
print("Đang tải file...")
gw.download_file(
    chat_id=-1003952062608,
    message_id=56,
    output_path="./downloads/movie.mp4",
    progress_callback=lambda current, total: print(f"{current*100//total}%", end="\r")
)
print("Hoàn tất tải file!")
```

---

## 6. Thiết Lập Cho Phép Toàn Bộ Kênh (Multi-Tenant)

Trong biến môi trường trên **Northflank**:
- Đặt `ALLOWED_CHAT_IDS=*` hoặc `ALLOW_ALL_CHATS=true`
- Khi đó, bất kỳ website hoặc tool nào của bạn có `STREAM_SIGNING_SECRET` đều có thể gọi tới bất kỳ channel Telegram nào mà tài khoản Telegram Gateway đang tham gia.
