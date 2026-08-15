# HƯỚNG DẪN CẤU TRÚC FILE VÀ CẬP NHẬT SERVER

1. THƯ MỤC GỐC

Bot_BaoHanh_MCP/
|
|-- app/
|   |-- channels/
|   |   |-- __init__.py
|   |   |-- base.py
|   |   |-- factory.py
|   |   |-- telegram_channel.py
|   |   `-- zalo_channel.py
|   |
|   |-- routes/
|   |   |-- __init__.py
|   |   |-- telegram_router.py
|   |   `-- zalo_router.py
|   |
|   |-- services/
|   |   |-- AI/
|   |   |   |-- providers/
|   |   |   |   |-- gemini_provider.py
|   |   |   |   `-- openai_provider.py
|   |   |   |-- base.py
|   |   |   `-- factory.py
|   |   |-- activation_service.py
|   |   |-- image_extraction_service.py
|   |   |-- MCP_Business_client.py
|   |   |-- order_service.py
|   |   |-- phone_validation_service.py
|   |   |-- telegram_service.py
|   |   `-- zalo_service.py
|   |
|   |-- config.py
|   |-- main.py
|   `-- models.py
|
|-- prompts/
|   |-- activation_conversation.txt
|   `-- image_order_extraction.txt
|
|-- data/
|   `-- activation_requests.json
|
|-- .env
|-- Dau_so_check.txt
|-- requirements.txt
|-- mcp_test.py
|-- test.py
|-- GHI_CHU_KIEN_TRUC.txt
`-- HUONG_DAN_CAU_TRUC_VA_DEPLOY.txt


2. CÁC FILE CHANNEL DÙNG CHUNG

app/channels/base.py
- Khai báo MessageChannel: giao diện chung cho Telegram và Zalo.
- Khai báo IncomingChannelMessage: dữ liệu tin nhắn chuẩn hóa.
- Khai báo ChannelLoggerAdapter: tự gắn channel=telegram/zalo vào log.

app/channels/factory.py
- Đọc danh sách channel được bật.
- Tạo TelegramChannel và/hoặc ZaloChannel.

app/channels/__init__.py
- Export các class channel để module khác import.


3. CÁC FILE TELEGRAM

app/channels/telegram_channel.py
- Adapter Telegram theo giao diện MessageChannel.
- Gửi tin, gửi typing, tải ảnh và parse webhook Telegram.
- Bên dưới sử dụng TelegramService.

app/services/telegram_service.py
- Gọi Telegram Bot API thật.
- Gửi tin nhắn, typing, lấy file và tải ảnh.

app/routes/telegram_router.py
- Endpoint: POST /api/telegram/webhook.
- Hiện vẫn chứa flow nghiệp vụ kích hoạt bảo hành.
- Nhận SĐT, mã đơn, ảnh, xác nhận và gọi activate_order.
- Log của file tự có channel=telegram.

Quan hệ:
telegram_router.py
  -> telegram_channel.py
  -> telegram_service.py
  -> Telegram Bot API


4. CÁC FILE ZALO

app/channels/zalo_channel.py
- Adapter Zalo theo giao diện MessageChannel.
- Hiện là placeholder và ready() trả False.
- Chưa parse payload webhook Zalo thật.

app/services/zalo_service.py
- Nơi sau này gọi Zalo OA API.
- Hiện chỉ đọc ZALO_ACCESS_TOKEN, ZALO_OA_ID, ZALO_WEBHOOK_SECRET.
- send_message, download_image, verify_webhook chưa triển khai.

app/routes/zalo_router.py
- Endpoint health: GET /api/zalo/health.
- Endpoint webhook: POST /api/zalo/webhook.
- Webhook hiện trả 503 vì Zalo chưa tích hợp.
- Log của file tự có channel=zalo.

Quan hệ sau này:
zalo_router.py
  -> zalo_channel.py
  -> zalo_service.py
  -> Zalo OA API


5. FILE KHỞI ĐỘNG VÀ CẤU HÌNH

app/main.py
- Khởi tạo AI.
- Gọi factory tạo channel theo BOT_CHANNELS.
- Nạp Telegram/Zalo router theo cấu hình.
- Endpoint chung: GET /health.

app/config.py
- Đọc .env.
- Chứa BOT_CHANNELS, MCP_URL, MCP_TIMEOUT và các đường dẫn.

Cấu hình mặc định:
BOT_CHANNELS=telegram

Chạy cả hai route (Zalo vẫn là placeholder):
BOT_CHANNELS=telegram,zalo

Không dùng AI_PROVIDER=zalo.
AI_PROVIDER chỉ dùng cho gemini/openai.


6. FILE NGHIỆP VỤ DÙNG CHUNG

app/services/order_service.py
- Gọi MCP get_order và get_customer.
- Chuẩn hóa mã đơn và SĐT.

app/services/activation_service.py
- Ghi data/activation_requests.json.
- Gọi MCP activate_order.
- Phân loại activated, already_activated, failed.

app/services/MCP_Business_client.py
- Kết nối MCP server và gọi tool.

app/services/phone_validation_service.py
- Kiểm tra SĐT theo Dau_so_check.txt.

app/services/image_extraction_service.py
- Kiểm tra và chuẩn hóa kết quả OCR ảnh.

app/services/AI/
- Chọn Gemini hoặc OpenAI.
- OCR ảnh và soạn hội thoại kích hoạt theo prompt.

prompts/activation_conversation.txt
- Rule soạn câu trả lời và phân loại confirm/cancel/unknown.

prompts/image_order_extraction.txt
- Rule OCR mã đơn và SĐT trên phiếu.


7. CÁC FILE PHẢI CẬP NHẬT LÊN SERVER ĐÃ DEPLOY

File mới cần thêm:
- app/channels/__init__.py
- app/channels/base.py
- app/channels/factory.py
- app/channels/telegram_channel.py
- app/channels/zalo_channel.py
- app/routes/zalo_router.py
- app/services/zalo_service.py

File cũ đã thay đổi cần thay thế:
- app/main.py
- app/config.py
- app/routes/telegram_router.py
- GHI_CHU_KIEN_TRUC.txt (tài liệu, không bắt buộc chạy bot)

Không ghi đè .env trên server bằng file .env từ máy local.
Chỉ thêm hoặc sửa biến BOT_CHANNELS trên .env của server.


8. BIẾN MÔI TRƯỜNG TRÊN SERVER

Telegram hiện tại:
BOT_CHANNELS=telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_SECRET=...

MCP và AI giữ nguyên:
MCP_URL=...
MCP_TIMEOUT=20
AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=...

Biến Zalo có thể để trống khi chưa tích hợp:
ZALO_ACCESS_TOKEN=
ZALO_OA_ID=
ZALO_WEBHOOK_SECRET=


9. CÁC BƯỚC CẬP NHẬT SERVER

1. Sao lưu source và .env hiện tại trên server.
2. Upload các file mới và file thay đổi đúng vị trí nêu trên.
3. Không xóa hoặc ghi đè data/activation_requests.json.
4. Thêm BOT_CHANNELS=telegram vào .env server.
5. Cài dependency nếu requirements.txt trên server chưa đủ:
   pip install -r requirements.txt
6. Kiểm tra cú pháp:
   python -m compileall -q app
7. Khởi động lại service/uvicorn theo cách server đang dùng.
8. Kiểm tra GET /health.
9. Test Telegram bằng tin nhắn không làm thay đổi đơn thật trước.


10. KẾT QUẢ HEALTH MONG ĐỢI

Với BOT_CHANNELS=telegram:
{
  "status": "ok",
  "enabled_channels": ["telegram"],
  "telegram_ready": true,
  "zalo_ready": false
}

Với BOT_CHANNELS=telegram,zalo:
- Router Zalo xuất hiện trên Swagger.
- GET /api/zalo/health trả ready=false.
- POST /api/zalo/webhook trả 503 cho đến khi tích hợp API thật.


11. LƯU Ý QUAN TRỌNG

- Telegram đang hoạt động thật.
- Zalo hiện chỉ là bộ khung, không nhận/gửi tin thật.
- Nghiệp vụ vẫn nằm trong telegram_router.py, chưa tách ra workflow chung.
- Trước khi tích hợp Zalo phải tách activation_workflow.py và state key
  theo channel:conversation_id.
- activate_order có thể thay đổi dữ liệu thật.
- Không được ghi token, secret hoặc API key ra log.
