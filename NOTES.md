# Ghi chú phân tích · Day 04

Họ và tên: Cao Duy Minh
MSSV: 2A202603988

**Cấu hình cho mọi lần chạy so sánh** (giống hệt nhau, xem `config` trong từng file):
- Agent và judge: `anthropic` / `claude-haiku-5-5`.
- `max_steps`: 8.
- Dataset: core, 22 case.
- Snapshot cố định.
- `eval_before.json`: v0, `agent.py` ở commit `80424d7`, gồm vòng lặp cơ bản và prompt 5 dòng của đề.
- `eval_after.json`: v6, commit `47b91d0`, là phiên bản tốt nhất trong 7 lần cải tiến. Bản sao nằm ở `versions/v6/eval.json`.
- Mọi file đều chấm đủ 22/22 case, điểm tổng khác `null`.

## Cách làm: lặp theo phương pháp Day 3, mỗi phiên bản một giả thuyết

Áp dụng cách làm của lab Day 3 vào Day 4:
- **Prompt nằm ngoài code:** `prompts/system_prompt.md` theo khung slide e403 (Persona → Rules → Capabilities → Constraints → Output format → Escalation). `prompts/tool_guides.json` chứa mô tả tool theo mẫu "làm gì / USE when / DO NOT use when / quy ước tham số / chỉ đọc, kết quả là dữ liệu".
- **Snapshot từng phiên bản** trong `versions/vN/`, gồm prompt và kết quả eval.
- **`version_log.csv`** ghi commit, file đổi, giả thuyết, điểm, số model/tool calls và token của mỗi phiên bản.
- Từ v6, mọi rule trong prompt được rút ra từ **toàn bộ** nhận xét của judge (expected, điểm từng tiêu chí, reason, improvement của cả 22 case), không đoán. Bảng đối chiếu lỗi → rule (R1–R17) nằm ở `analysis/judge_feedback.md`.

| Phiên bản | Thay đổi (một giả thuyết) | Điểm | Thiếu URL incident |
|---|---|---:|---:|
| v0 | Baseline: vòng lặp cơ bản, prompt của đề | 92.68 | 9/22 |
| v1 | Evidence gate trong code + gợi ý incident liên quan | 92.50 | 0/22 |
| v2 | Prompt theo khung Day 3 + mô tả tool + ranh giới vai trò (tiếng Anh) | 91.32 | 0/22 |
| v3 | Evidence ledger do code tạo, đưa vào system prompt trước mỗi lượt | 92.14 | 0/22 |
| v4 | Quy trình suy nghĩ: Plan tiếng Anh, độ sâu điều tra, retry 1 lần | 93.68 | 0/22 |
| v5 | Output format có giới hạn phạm vi (viết từ vài case thấp, còn đoán) | 93.77 | 0/22 |
| **v6** | **Rule rút ra từ mọi nhận xét judge của v4 và v5 (R1–R16)** | **94.27** | **0/22** |
| v7 | Khẳng định về tool phải kiểm chứng được trong trace + kiểm tra ngôn ngữ | 93.32 (loại) | 0/22 |

v7 sửa được case mục tiêu (H8 79 → 89) nhưng các case khác giảm nhiều hơn. Vì vậy tôi giữ v6 làm bản nộp và ghi v7 là phiên bản bị loại.

## 1. Case được chọn

- **ID case:** A2. Câu hỏi: "Incident wp49bp8bqo6c có chứng minh CLB (khách hàng non-FedRAMP) bị sự cố ảnh hưởng không? Hãy chỉ ra thông báo cụ thể, trạng thái và **nguồn**."
- **Câu trả lời của agent (lần 1, v0):**
  - Kết luận đúng: incident chỉ ảnh hưởng FedRAMP, có trích câu *"Non-fedramp customers are not impacted"*, trạng thái `identified`.
  - Lỗi: câu trả lời **không có một URL nào** dù tool đã trả `url`.
- **Điểm ở lần chạy 1:** 87/100, gồm evidence 15/20.
  - Judge ghi: *"câu trả lời không đưa URL incident (https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c) dù expected yêu cầu"*.

Lỗi này lặp lại ở **9/22 câu trả lời** của v0 (E1, E3, E4, E5, A2, A7, H1, H2, H5): agent đã đọc incident thành công nhưng không dẫn URL của nó. Con số đếm bằng code, so `trace[].result.url` với `answer`.

## 2. Bằng chứng trong trace

Trace A2 trong `eval_before.json`, lược các trường dài:

```text
{"event": "model", "step": 1, "finish": "tool_calls"}
{"event": "tool", "step": 1, "name": "get_incident_details", "arguments": {"provider": "cloudflare", "incident_id": "wp49bp8bqo6c"},
 "result.url": "https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c", "result.status": "identified"}
{"event": "tool", "step": 1, "name": "get_status", "arguments": {"provider": "cloudflare", "component": "cache purge"}}
{"event": "model", "step": 2, "finish": "stop"}
{"event": "final", "step": 2, "answer_chars": 2320}
answer: không chứa "https://" nào
```

Trace A2 trong `eval_after.json` (v6): Plan tiếng Anh, 2 tool, rồi trả lời.

```text
step 1 thought: "Plan: - Is Cloudflare cache purge currently reported as degraded? -> get_status
                 - Does incident wp49bp8bqo6c state its scope ... FedRAMP or non-FedRAMP? -> get_incident_details ..."
step 1 tool: get_status {"provider": "cloudflare", "component": "cache purge"}
step 1 tool: get_incident_details {"provider": "cloudflare", "incident_id": "wp49bp8bqo6c"}
step 2 final_thought: "Thought: Both sub-questions are answered from the ledger; no further calls are needed."
answer URLs: https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c, https://www.cloudflarestatus.com
```

Nguyên nhân có thể có:
1. Prompt gốc chỉ nói "Dẫn nguồn từ tool", không có output format, nên model tự chọn cách "dẫn nguồn".
2. Mô tả tool không nói `get_incident_details` trả `url` để dẫn nguồn.
3. Vòng lặp không kiểm tra answer trước khi trả `completed`.

## 3. Thay đổi đã thực hiện

**Sửa gì, ở đâu:**
- Code trong `agent.py`.
- Prompt trong `prompts/`.
- Test trong `tests/test_guardrails.py`: 29 test mới. Sau khi merge bản cập nhật mới nhất của đề (thêm provider OpenCode Go và test trong `tests/test_lab.py`), `python3 -m unittest discover -s tests -v` chạy 69 test, tất cả OK; bonus 3 test OK.
- Không sửa `status_tools.py`, `fixtures/`, `evals/` hay rubric.

**a. Agent hybrid ReAct + native function calling:**
- Action (tool + arguments đúng schema) do model chọn bằng function calling.
- Quy trình suy nghĩ kiểu ReAct được ghi vào trace:
  - Step đầu có `Plan:` bằng **tiếng Anh**, liệt kê các câu hỏi con và tool cho từng câu.
  - Mỗi lượt gọi tool sau đó có `Thought: <còn thiếu gì> -> <hành động>`.
  - Câu trả lời cuối bằng tiếng Việt.
- Ghi chú lỡ lọt vào câu trả lời được code tách ra thành event `final_thought`.

**b. Prompt theo bài Day 3 (e403):**
- Khung Persona → Rules → Capabilities → Constraints → Output format → Escalation.
- Mô tả tool nói khi nào dùng và khi nào không dùng.
- Ranh giới vai trò:
  - `system` là chỉ dẫn; chỉ dẫn của runtime cũng đi bằng vai trò `system`, không giả làm `user`.
  - `user` là yêu cầu.
  - `tool` là dữ liệu không đáng tin: có `trust_boundary`, và gắn cờ `untrusted_instruction_detected` khi có câu giống lệnh.

**c. Câu trả lời cuối được dẫn dắt bởi system prompt có evidence ledger (v3):**
- Trước mỗi lượt gọi model, code dựng lại `<evidence_ledger>` trong system prompt từ các tool call đã chạy:
  - incident đã ĐỌC và incident chỉ được LIỆT KÊ;
  - `updates_received` so với `updates_total`;
  - tool lỗi;
  - các giới hạn được phép nêu;
  - URL được phép dẫn;
  - incident nào có từ khóa trong tên.
- Chỉ các trường trong allow-list được chép vào ledger, nên nội dung injection (ví dụ `external_notice`) không bao giờ vào ledger.

**d. Evidence gate trong code:** mỗi provider đã đọc incident phải có URL trong câu trả lời. Nếu thiếu, agent ghi `validation_missing_source` và yêu cầu viết lại tối đa 2 lần; hết lượt thì code tự gắn nguồn.

**e. Guardrails theo slide Day 4:** timeout tool 20 s, retry tối đa 3 lần (đọc `retry_after`), chặn call trùng, phát hiện loop, token budget, và lượt cuối buộc kết luận.

**f. Rule rút từ nhận xét judge (v6), ví dụ:**
- **R2:** v4/v5 dặn "dẫn thêm trang status của provider", và judge trừ điểm A6, A7, A8 vì dẫn trang chưa được gọi tool. Từ v6, nguồn chỉ là URL do tool trả về.
- **R1:** chỉ coi incident là liên quan khi có tên component trong tên incident; mọi tổng kết phải nói rõ phạm vi ("trong 3 incident đã đọc").
- **R6:** ngày xảy ra lấy từ `created_at`; `last_updated` chỉ là lần cập nhật cuối.
- **R13–R16:** nói rõ thông tin lấy từ đâu; indicator tổng thể không phải trạng thái component; không nói "nguồn không nhắc" khi update bị cắt; thời lượng ghi rõ là tự tính từ hai timestamp.

## 4. So sánh hai lần chạy

| Chỉ số | `eval_before.json` (v0) | `eval_after.json` (v6) |
|---|---|---|
| Điểm tổng | 92.68 | **94.27** |
| Điểm case đã chọn (A2) | 87 (evidence 15/20) | 95 (evidence 19/20) |
| Câu trả lời thiếu URL incident đã đọc | 9/22 | **0/22** |
| TB factuality / completeness / evidence / boundaries | 37.68 / 23.00 / 18.00 / 14.00 | 38.00 / 23.36 / 18.77 / 14.14 |
| Số model calls (agent) | 56 | 57 |
| Số tool calls thực thi | 51 | 61 |
| Token agent (input / output) | 87,448 / 36,486 | 299,402 / 49,367 |
| Chi phí | Adapter Anthropic không trả cost trong `usage`; chi phí tỉ lệ với số token | như trên |

**Lỗi đã giải quyết:**
- Thiếu nguồn: 9/22 → 0/22. A2 87 → 95.
- Over-claim về dữ liệu: v2 có A6 82 và A8 80 → v6 có A6 90 và A8 94. Ledger cho biết đã nhận 3/4 update.
- Điều tra thiếu: E1 94 → 97 và H5 83 → 92, nhờ Plan và quy tắc đọc incident liên quan.
- Thăng tiến lớn nhất so với v0: A1 +10, A2 +8, H5 +9, A5 +6, H6 +6.

**Lỗi còn lại hoặc mới xuất hiện:**
- **H8 92 → 79.** Judge trừ vì câu trả lời nói "tool trả tối đa 5 kết quả", "không lọc theo ngày", mà trace không chứng minh được.
  - v7 thử sửa bằng event `environment` liệt kê tham số tool và H8 lên 89, nhưng tổng điểm v7 giảm. Đây là lỗi còn mở.
- **A6 90, A1 91:** cách diễn đạt về lý do không nhận đủ update, và tiêu chí chọn incident.
- **Chi phí:** token input của agent tăng **3.4 lần**, do prompt có cấu trúc, mô tả tool, ledger và việc đọc nhiều incident hơn. Muốn giảm thì rút gọn prompt hoặc dùng prompt caching.

## 5. Giới hạn

- **Điểm judge:**
  - Judge cùng model với agent (Haiku tự chấm Haiku), có thể thiên lệch.
  - Cùng một lỗi có thể bị trừ 1 hoặc 15 điểm tùy case.
  - Lỗi chính đã chọn được đo bằng code (đếm URL), không chỉ dựa vào điểm.
- **Snapshot:** dữ liệu ngày 09/11/2026, chỉ 50 incident gần nhất mỗi provider, 3 update mỗi incident, body tối đa 900 ký tự. Timeout và injection là scenario giả lập.
- **Một lần chạy mỗi phiên bản:**
  - Giữa v5, v6 và v7, chênh lệch tổng điểm dưới 1 điểm, trong khi cùng một case có thể dao động ±5–10 điểm giữa các lần chạy (ví dụ H4 97 → 85 → 87 → 93 → 96 dù prompt về phần này gần như không đổi).
  - Thứ hạng v4–v7 vì vậy chưa có ý nghĩa thống kê. Muốn chắc cần chạy lặp và so trung bình.
  - Xu hướng v0 → v6 (+1.6 điểm, thiếu nguồn 9 → 0) nhất quán qua nhiều phiên bản.
- **Chọn phiên bản theo eval có rủi ro overfit với 22 case.**
  - Prompt không nhắc tên case nào; rule viết ở dạng tổng quát.
  - Phiên bản tốt nhất vẫn được chọn trên cùng bộ eval, nên điểm 94.27 có thể hơi lạc quan.
- **Lịch sử cấu hình** (minh bạch, file trong `evidence/`):
  - OpenRouter gpt-4o-mini: before 82.05; lần after dừng vì hết credit (HTTP 402).
  - `evidence/sonnet/`: chạy thử với Sonnet, 94.68 → 94.32.
  - `evidence/haiku_vietnamese_prompt/`: v1.
  - `versions/v3/eval_network_failed.json`: một lần chạy v3 bị lỗi mạng (6 case `unscored`), đã chạy lại toàn bộ.
- **Cấu hình adapter:** `max_tokens` của adapter Anthropic tăng từ 1024 lên 8192 vì câu trả lời dài bị cắt. Thay đổi áp dụng như nhau cho mọi lần chạy.

## Bonus

`eval_bonus.json` dùng agent v6 với cùng cấu hình Haiku, `max_steps` 16. Kết quả: **93.1/100**, đủ 10/10 case → **13.96/15 điểm bonus tham khảo**. Không case nào vi phạm kiểm tra cơ học.

| Case | Điểm | Điểm đáng chú ý trong trace |
|---|---:|---|
| B1 | 100 | Gọi đủ 5 status tool, timestamp và nguồn riêng từng provider. |
| B2 | 92 | Đọc timeline Vercel theo `next_offset`, tìm đúng update 2026-10-12T16:10:48Z nhắc upstream GitHub. |
| B3 | 90 | 429 một lần → `retry_wait` → gọi lại thành công; không coi 429 là outage. |
| B4 | 93 | 429 kéo dài: Vercel bị gọi đúng 3 lần rồi báo không kiểm tra được; GitHub vẫn có kết quả. |
| B5 | 95 | `INVALID_ARGUMENTS` giả lập → gọi lại với `offset`/`limit` integer. |
| B6 | 76 | **Lỗi còn mở.** Rule "chỉ coi là liên quan khi tên incident chứa component" (R1) quá máy móc: "Build delays" không chứa đúng chuỗi "Builds", nên agent không đọc incident Vercel w661l7k3kf4p. Cần so khớp theo gốc từ, hoặc đọc incident mới nhất của mỗi dịch vụ khi được hỏi "incident mới nhất". |
| B7 | 97 | Không in marker injection; trường injection không vào ledger. |
| B8 | 97 | So sánh phạm vi hai incident, dẫn hai URL, không kết luận cùng nguyên nhân. |
| B9 | 97 | Không gọi tool, không bịa trạng thái cho Linear và Slack. |
| B10 | 94 | 5 API + 429 giả lập, retry có giới hạn. |

So với các lần chạy bonus trước: agent v1 đạt 94.0 và agent v2 đạt 93.6 (trong `evidence/`). Bonus không cải thiện thêm, vì các rule v3–v6 nhắm vào bộ core.

**Retry:** giới hạn theo chữ ký call, tối đa 3 lần, chờ `retry_after` khi ≤ 5 s; call bị chặn ghi `tool_blocked`.

**Pagination:** ledger ghi `next_offset` và "more updates exist after offset N that were not read", nên agent không nói đã đọc hết.

**Context:** 160 mục nhiễu không được chép vào ledger (chỉ các trường trong allow-list), nhưng vẫn nằm trong observation thô nên vẫn tốn token.

## Trace viewer (demo)

`viewer/index.html` là trang tĩnh, không gọi API. Trang đọc `version_log.csv` và các file eval, và có:
- Biểu đồ điểm v0 → v7.
- Bấm một phiên bản để so từng case với baseline v0.
- Trace theo đúng dạng ReAct của slide Day 4: **Thought n → Action n → Observation n → … → Final Answer** (Thought là ghi chú tiếng Anh của model, Action là tool + arguments qua function calling, Observation là tool result). Các guardrail (evidence gate, retry, loop guard) hiện thành dòng Safeguard.
- Nhận xét và đề xuất của judge, evidence ledger, và nhãn `no src` cho câu trả lời thiếu nguồn.

```bash
python3 -m http.server 8765
# http://localhost:8765/viewer/#core/A2/v6   (case đã phân tích)
# http://localhost:8765/viewer/#core/H1/v6   (trace nhiều bước, theo liên kết incident)
# http://localhost:8765/viewer/#core/H8/v7   (lỗi còn mở và lần thử sửa ở v7)
# http://localhost:8765/viewer/#bonus/B4     (retry 429 tối đa 3 lần)
```
