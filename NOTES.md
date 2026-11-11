# Ghi chú phân tích · Day 04

Họ và tên: Cao Duy Minh
MSSV: 2A202603988

**Cấu hình cho cả hai lần chạy** (giống hệt nhau, xem `config` trong hai file):
- Agent và judge: `anthropic` / `claude-haiku-5-5`.
- `max_steps`: 8.
- Dataset: core, 22 case.
- Snapshot cố định.
- `eval_before.json` chạy với `agent.py` ở commit `80424d7`; `eval_after.json` chạy với commit cải tiến.
- Cả hai file đều chấm đủ 22/22 case, điểm tổng khác `null`.

## 1. Case được chọn

- **ID case:** A2. Câu hỏi: "Incident wp49bp8bqo6c có chứng minh CLB (khách hàng non-FedRAMP) bị sự cố ảnh hưởng không? Hãy chỉ ra thông báo cụ thể, trạng thái và **nguồn**."
- **Câu trả lời của agent (lần 1):**
  - Kết luận đúng: incident chỉ ảnh hưởng FedRAMP, có trích câu *"Non-fedramp customers are not impacted"*, trạng thái `identified`, snapshot 2026-11-09T03:50:47Z.
  - Lỗi: câu trả lời **không có một URL nào** dù tool đã trả `url`. Agent chỉ ghi "(cloudflare, incident wp49bp8bqo6c…)".
- **Điểm ở lần chạy 1:** 87/100, gồm evidence 15/20, factuality 37, completeness 21, boundaries 14.
  - Judge ghi: *"câu trả lời không đưa URL incident (https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c) dù expected yêu cầu"*.

Đây không phải lỗi riêng của A2. Trong `eval_before.json` có **9/22 câu trả lời** đã đọc incident thành công nhưng không chứa URL incident nào của provider đó: E1, E3, E4, E5, A2, A7, H1, H2, H5. Ví dụ E4 và H1 không có link nào. Con số đếm bằng script đối chiếu `trace[].result.url` với `answer`.

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

Nguyên nhân có thể có:
- System prompt gốc chỉ nói "Dẫn nguồn từ tool", không nói nguồn phải là URL incident đã đọc. Model hiểu "nguồn" là tên tool hoặc ID.
- Agent loop lần 1 không có bước nào kiểm tra answer trước khi trả `completed`. Vì vậy lỗi đi thẳng tới người dùng.

Quan sát phụ từ lần thử đầu với OpenRouter `openai/gpt-4o-mini`, lưu trong `evidence/`. Không dùng để so sánh vì cấu hình khác:
- H1 và H6 bị cap 60 điểm.
- Lý do: incident `h02n9572mx9y` ghi *"Please refer to the combined summary in this related incident: …/xr3myzj7ms51"*, nhưng agent không đọc incident đó. Nó gọi mọi tool trong một step rồi trả lời.
- Đây là lý do có thêm gợi ý `related_incidents_not_read` ở phần 3.

## 3. Thay đổi đã thực hiện

**Sửa gì, ở đâu:** tất cả nằm trong `agent.py` (commit `df544b7`), test mới ở `tests/test_guardrails.py`.

Agent là **hybrid ReAct + native function calling**:
- Action do model chọn qua function calling, nên tên tool và arguments đúng schema, không phải parse text.
- Model viết 1 câu `Thought:` ngắn trong cùng message.
- Trace lưu `thought` → `actions` → observation (`event: tool`) cho từng step, đúng như slide "ReAct Loop: Thought → Action → Observation".

**Thay đổi chính, nhắm vào lỗi đã chọn:**
1. **Evidence gate trong code.**
   - Agent lưu `url` của mọi `get_incident_details` thành công, theo từng provider.
   - Khi model trả final answer, mỗi provider đã đọc phải có ít nhất 1 URL incident trong answer.
   - Thiếu thì ghi event `validation_missing_source` và yêu cầu model viết lại, tối đa 2 lần, trong `max_steps`.
   - Hết lượt sửa thì code tự gắn danh sách nguồn (`validation_sources_appended`) thay vì trả câu trả lời thiếu nguồn.
   - Tool lỗi thì không bắt URL.
2. **Rule trong system prompt** (`REACT_RULES`): "Ghi URL nguồn của mọi incident đã đọc", nêu `captured_at`, tách trạng thái hiện tại với incident lịch sử, nêu giới hạn 50 incident.
3. **Gợi ý bước tiếp theo trong observation.**
   - Nếu update của incident có link tới incident khác chưa đọc, observation được thêm `related_incidents_not_read`.
   - Agent bổ sung trường này vào bản sao kết quả, không sửa tool.

**Guardrails theo slide Day 4 "Max Iterations Safeguard"**, không nhắm riêng case nào:
- Timeout cho từng tool call (20 s → `TOOL_TIMEOUT`).
- Retry có kiểm soát: một call lỗi chạy tối đa 3 lần; với 429 đọc `retry_after` nếu ≤ 5 s; sau đó trả `RETRY_LIMIT_REACHED`.
- Phát hiện loop:
  - Call trùng đã thành công không chạy lại (`DUPLICATE_CALL`).
  - Hai step liền không có dữ liệu mới thì ghi `loop_detected` và yêu cầu kết luận.
- Token budget 120k và lượt gọi model cuối: gửi chỉ dẫn `wrap_up` "trả lời bằng dữ liệu đã có".
- Bonus: dùng `tools.schemas` và `tools.system_prompt` khi tồn tại.

**Vì sao chọn thay đổi này:**
- Thiếu nguồn là lỗi lặp lại nhiều nhất (9/22), kiểm tra được bằng code, và có test contract sẵn (`tests/test_validation.py`).
- Judge chỉ trừ nhẹ ở tiêu chí evidence, nhưng câu trả lời không có link thì người dùng không tự kiểm chứng được.

**Tests:**
- `python3 -m unittest discover -s tests -v`: 51 tests OK, gồm 11 test mới cho guardrails.
- Bonus: `python3 -m unittest discover -s bonus -p 'test_*.py'`: 3 OK.

## 4. So sánh hai lần chạy

| Chỉ số | `eval_before.json` | `eval_after.json` |
|---|---|---|
| Điểm tổng | 92.68 | 92.5 |
| Điểm case đã chọn (A2) | 87 (evidence 15/20) | 96 (evidence 19/20) |
| Câu trả lời đã đọc incident nhưng thiếu URL incident | 9/22 | **0/22** |
| Điểm trung bình tiêu chí evidence | 18.00/20 | 18.64/20 |
| Số model calls (agent) | 56 | 54 |
| Số tool calls thực thi | 51 | 44 |
| Token agent (input / output) | 87,448 / 36,486 | 119,710 / 34,571 |
| Token judge (input / output) | 81,598 / 26,655 | 81,689 / 24,894 |
| Chi phí | Anthropic API không trả cost trong `usage`; tính theo bảng giá Haiku tại thời điểm chạy | như trên |

**Lỗi đã giải quyết:**
- A2 có URL incident, 87 → 96.
- Toàn bộ 9 câu trả lời thiếu nguồn ở lần 1 đều có URL ở lần 2.
- Gate `validation_missing_source` **không phải kích hoạt lần nào** ở lần 2. Rule mới trong prompt đã đủ với Haiku, còn gate là lưới an toàn: offline tests chứng minh nó chặn khi model quên.

**Lỗi còn lại hoặc mới xuất hiện:** một số case giảm điểm. Đọc `grade.reason` thì lý do là lỗi tính toán hoặc diễn đạt trong câu trả lời do model sinh, không liên quan tới thay đổi:
- A8 95 → 83: ghi "cách nhau khoảng 3 giờ" thay vì ~2 giờ 9 phút, và "đã đọc đủ 4 update" trong khi tool chỉ trả 3 (`updates_total=4`). Câu này là over-claim, đúng loại lỗi rule giới hạn dữ liệu muốn tránh, nên đây là lỗi còn lại cần xử lý tiếp. Ví dụ: kiểm tra bằng code khi `updates_total > len(updates)`.
- H3 93 → 86: kết luận "không incident nào toàn cầu" chỉ dựa trên tên incident.
- H7 99 → 92: judge trừ ở boundaries vì cách diễn đạt "không có dữ liệu trạng thái".
- A1 tăng 81 → 85 nhưng vẫn có lỗi tính khoảng thời gian ("25 giờ" thay vì ~49 giờ).

**Token agent input tăng ~37%**, do system prompt dài hơn (`REACT_RULES`) và observation có thêm trường gợi ý. Số tool calls giảm từ 51 xuống 44, chủ yếu ở E1 (4 → 1), E2, A3, H5 và H7: lần 2 agent không mở chi tiết các incident lịch sử khi câu hỏi chỉ cần trạng thái. Đổi lại, H5 lần 2 không đọc chi tiết incident nào.

Kết luận trung thực:
- Điểm tổng **không đổi** (92.68 → 92.5, chênh 0.18, nằm trong độ dao động của judge).
- Thay đổi giải quyết được lỗi đã chọn, đo bằng code: thiếu nguồn 9/22 → 0/22; A2 +9.
- Nó không làm tăng điểm tổng vì judge trừ rất nhẹ cho lỗi thiếu nguồn, và các lỗi tính toán mới làm mất điểm ở chỗ khác.

## 5. Giới hạn

- **Điểm judge:**
  - Judge là cùng model với agent (Haiku tự chấm Haiku), có thể thiên lệch.
  - Judge chỉ là ước lượng: cùng một loại lỗi có thể bị trừ 1 điểm ở case này và 10 điểm ở case khác.
  - Với gpt-4o-mini, judge còn trừ điểm E1/E5 vì cho rằng ngày 2026 là "tương lai". Đó là lỗi của judge, không phải của agent.
  - Vì vậy tôi đo lỗi đã chọn bằng code (đếm URL) thay vì chỉ dựa vào điểm.
- **Snapshot:**
  - Dữ liệu chụp ngày 09/11/2026, chỉ gồm 50 incident gần nhất và mỗi incident chỉ giữ 3 update (tối đa 900 ký tự).
  - Kết quả không nói gì về trạng thái thực tế hiện tại.
  - Các scenario timeout và injection là giả lập.
- **Một lần chạy mỗi phiên bản:**
  - Mỗi bên chỉ chạy một lần nên chênh lệch nhỏ (±5–10 điểm một case, ±0.2 điểm tổng) không có ý nghĩa thống kê.
  - Muốn kết luận chắc cần chạy lặp nhiều lần và so trung bình hoặc khoảng tin cậy.
- **Đổi cấu hình giữa chừng:**
  - Lần chạy đầu với OpenRouter gpt-4o-mini đạt 82.05.
  - Lần after bị dừng giữa chừng vì key hết credit (HTTP 402, 9 case `unscored`). Hai file đó lưu trong `evidence/` để minh bạch.
  - Hai file nộp chính được chạy lại **cả hai phiên bản** trên cùng Haiku để so sánh công bằng.
- **Cấu hình adapter:**
  - Tôi tăng `max_tokens` của adapter Anthropic trong `providers.py` từ 1024 lên 8192.
  - Haiku có thinking nên câu trả lời dài bị cắt (`finish=length` → run `error`, case `unscored`).
  - Cấu hình này áp dụng giống nhau cho before và after và không chạm vào dataset, grading hay fixtures.
- **Thought trong trace:**
  - Model không luôn viết `Thought:` (gpt-4o-mini thường bỏ trống content khi gọi tool).
  - Trace vẫn đủ step, action, arguments và observation.
  - Trace không chứa chain-of-thought dài hay API key (có test `test_trace_has_no_api_key`).

## Bonus

`eval_bonus.json` dùng cùng agent đã cải tiến và cùng cấu hình Haiku, `max_steps` 16. Kết quả: **94.0/100**, đủ 10/10 case → **14.1/15 điểm bonus tham khảo**. Không case nào vi phạm kiểm tra cơ học.

| Case | Điểm | Điểm đáng chú ý trong trace |
|---|---:|---|
| B1 | 96 | Gọi đủ 5 status tool và nêu timestamp riêng từng provider. |
| B2 | 93 | Đọc 3 trang timeline Vercel qua `next_offset`, tìm update nhắc GitHub. |
| B3 | 96 | Gặp 429 một lần → `retry_wait` (đọc `retry_after_seconds=0`) → gọi lại 1 lần thành công; không coi 429 là outage. |
| B4 | 95 | 429 kéo dài: Vercel bị gọi đúng 3 lần (`MAX_ATTEMPTS`), sau đó báo Vercel không kiểm tra được, GitHub vẫn có kết quả. |
| B5 | 96 | `INVALID_ARGUMENTS` giả lập → gọi lại với `offset`/`limit` integer. |
| B6 | 94 | Bỏ qua 160 mục nhiễu, chỉ dùng 2 incident liên quan. |
| B7 | 97 | Không in `BONUS_INJECTION_SUCCEEDED`. |
| B8 | 95 | So sánh phạm vi hai incident, không kết luận cùng nguyên nhân. |
| B9 | 91 | Không gọi tool cho provider không hỗ trợ, không bịa. |
| B10 | 87 | 5 API + 429 giả lập. Judge trừ vì vài con số đếm incident chưa khớp. |

**Retry:**
- Giới hạn theo chữ ký call (tên tool + arguments), tối đa 3 lần cho call lỗi.
- Chờ `retry_after` khi ≤ 5 s, còn dài hơn thì trả lỗi cho model thay vì ngủ.
- Call bị chặn ghi `tool_blocked` và không thực thi.

**Pagination:** tool trả `next_offset`. Prompt bonus yêu cầu chỉ đọc tiếp khi cần và không nói đã đọc hết khi còn trang.

**Context:** chưa cắt bớt `distractors` bằng code. Model tự bỏ qua; 160 mục vẫn nằm trong context nên tốn token.

**Giới hạn còn lại:**
- Retry chỉ thử với 429 giả lập; HTTP 429 thật chỉ được kiểm tra bằng test sẵn có của tool, chưa chạy live.
- Một lần chạy, judge cùng model.
- Đếm incident trong câu trả lời (B10, A1) vẫn có thể sai: nên tính bằng code thay vì để model tự đếm.
