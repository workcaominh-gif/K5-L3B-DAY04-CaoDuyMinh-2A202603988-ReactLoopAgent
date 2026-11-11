# Judge feedback → prompt rules (from `versions/v4/eval.json`, 22/22 cases)

Method: read `expected`, `grade.dimensions`, `grade.reason` and `grade.improvement` of every case, group each
deduction by root cause, and write one rule per group. Every rule below cites the cases whose judgment
motivated it. Cases with no deduction in a group are not listed. Quotes are shortened.

| # | Deduction pattern (judge's words) | Cases | Root cause | Rule for the prompt |
|---|---|---|---|---|
| R1 | "gọi cả 5 là khớp Actions là khẳng định quá mức" (smtdswd1h2w4 has no Actions in its name); pfbmpxxfq1cy called "related" without reading; "3 sự cố gần nhất" with no stated criterion; time range "4–5/11" extended to an unread incident; "không có incident đang mở" after looking up one ID | E1, A1, A3, H5, E4, A4, E5 | The model generalises from a search result list to "related", "most recent", "all", "none open" | An incident is related to a component only if the component appears in its name or components; otherwise "returned by the search, relevance not verified". Every summary names the set it covers ("among the 3 incidents read"). State the selection criterion when picking incidents. Never claim "no open incident" beyond the incidents actually checked. |
| R2 | "ghi 'Trang trạng thái GitHub đã kiểm tra' trong khi trace chỉ có get_incident_details"; "đã kiểm tra trang status dù dữ liệu lấy qua API"; "nêu thêm trang trạng thái chung chưa được gọi tool"; history URLs "không xuất hiện trong trace" | A6, A7, A8, H8 | **Our v4 output format told the model to add "the status page of each provider you checked"**, and to suggest official pages | Sources = only the URLs listed under "Sources to cite" in the ledger. Never say you checked a status page; data came from the status API through tools. Any other URL is a suggestion and must be labelled "not verified in this session". |
| R3 | "'không phải outage' hơi khẳng định hơn mức nguồn cho phép, nguồn chỉ mô tả degraded performance"; "'không phải toàn cầu' … dữ liệu không nêu phạm vi"; opening "Có" misleads; "CLB nằm ngoài phạm vi" too strong | H2, H3, E2, A2 | The model turns the source's wording into its own stronger verdict | Describe severity and scope in the source's own words ("the source describes degraded performance"). If the data does not state something (global or not, affects the user or not), say the data does not state it. The opening sentence answers the exact question with its scope. |
| R4 | "'Cloudflare đang điều tra' phản ánh cập nhật ban đầu trong khi trạng thái hiện tại là monitoring"; resolved incident described as "đang theo dõi" | E3, H6 | Update content is mixed up with the incident's current status | Give the incident's current status (its status field) first. When citing an update, give that update's own status and timestamp ("the first update (investigating, 2026-11-06T17:47Z) said …"). |
| R5 | "phần trích dẫn nguồn thực chất là bản dịch … vẫn đặt trong ngoặc kép" | E3 | Translated text presented as a verbatim quote | Quote verbatim (English) inside quotation marks; a Vietnamese rendering is marked as a translation. |
| R6 | "năm được suy ra từ updated_at, không phải thời điểm bắt đầu"; "các mốc thời gian lấy từ updated_at nhưng được trình bày như thời điểm incident" (v2/v3 E1) | A5, E1 | `last_updated` read as the incident date | When something happened comes from `created_at`; `last_updated` is only the last update. Name the field when a date or year is inferred. |
| R7 | "'Đã xác nhận' dựa trên đoạn cập nhật đã bị cắt"; "đọc thêm các bản cập nhật còn lại … phần mô tả nguyên nhân bị cắt" | A6, H1 | Conclusions drawn from truncated text without saying so | A cause or fact taken from a truncated update is labelled "from a truncated update". |
| R8 | "các mục trùng ý (trạng thái snapshot, phạm vi ảnh hưởng) … rút gọn thành một câu nêu rõ không có dữ liệu" | E6 | Fixed 5-section format filled with empty sections | Omit sections that have no data. If nothing could be checked, answer in a few lines. |
| R9 | "từ chối hơi cứng và không đưa ra gợi ý nào hữu ích khác" | H4 (v3, v4) | Out-of-scope reply has no alternative | Out-of-scope: one friendly sentence, no status data, and suggest what you can do instead (or that another assistant can help). |
| R10 | "chưa nêu rõ việc đối chiếu log lỗi phía người dùng với khung thời gian created_at/updated_at của incident, và … traceroute" | A7 | Generic next steps | For "did incident X cause my problem" questions, next steps are concrete: compare your error timestamps/logs with the incident's created_at–last_updated window, and test the network path to the affected location. |
| R11 | "Nêu rõ hơn rằng yêu cầu 'không được nói thiếu dữ liệu' không thể thực hiện vì nó sẽ làm sai kết luận" | H7 | Refusal of an unsupported user instruction is implicit | When the user asks you to state something the data does not support, say briefly that you cannot and why. |
| R12 | "Câu nhắc Cloudflare không cần thiết" | E1 | Answer drifts to providers not asked about | Only report on providers and components the user asked about. |

## Added after reading every v5 judgment (`versions/v5/eval.json`, 93.77)

| # | Deduction pattern (judge's words) | Cases | Rule for the prompt |
|---|---|---|---|
| R13 | "nội dung update 'vẫn nêu lỗi liên quan Copilot Mission Control', trong khi các update đã đọc không nhắc tên sản phẩm này; chỉ tiêu đề có"; "nguyên nhân trong xr3myzj7ms51 là mô tả của incident đó" | E5, A6 | Say where each fact comes from (title, update, components, overall indicator) and from which incident. |
| R14 | "'Partial System Outage' là trạng thái toàn trang Cloudflare, không phải của riêng Spectrum" | A1 | The overall indicator describes the provider page, not a component. |
| R15 | "'công cụ chỉ trả về tối đa 5 incident' không có trong trace"; "matching_incidents_in_last_50 = 50 không được giải thích"; "không nêu rõ công cụ không hỗ trợ lọc theo khoảng thời gian" | H8, A5, H3 | Give counts exactly as returned (N matching, M returned) without inventing why; describe tool limits only from its parameters. |
| R16 | "thời lượng Cloudflare là làm tròn"; "ghi rõ thời lượng là giá trị suy ra từ timestamp" | H1, A8 | Durations are labelled as computed from two named timestamps, to the minute. |
| R7b | "'nguồn không nói rõ thêm về nguyên nhân gốc' trong khi update đó bị cắt ngắn" | E4 | With truncated or missing updates, say "not in the part received", never "the source does not mention". |
| R11b | "nêu ngay trong phần kết luận rằng Cloudflare Spectrum trả về TimeoutError … và lý do không thể đánh dấu operational" | H7 | Name the failed part and its error in the conclusion. |
| R1b | "gọi ba incident là 'gần đây nhất' trong khi pfbmpxxfq1cy mới hơn" | E1, H5 | State the selection criterion ("the 3 most recently updated incidents whose name contains Actions"), never just "the most recent". |

## Added after reading every v6 judgment (`versions/v6/eval.json`, 94.27)

| # | Deduction pattern (judge's words) | Cases | Fix in v7 |
|---|---|---|---|
| R15b | "'tối đa 5 kết quả mỗi lần', 'không lọc theo ngày' … không được trace xác nhận trực tiếp" (79 điểm); "ghi 'limit từ tool' là lý do không có trong trace" | H8, A6 | **Code:** the trace now starts with an `environment` event listing each tool's parameters, and the ledger repeats them, so "the tool accepts only provider and component" is checkable. **Prompt:** tool abilities only from those lines; numbers without reasons; never state a maximum or page size. |
| R1c | "ba incident này là toàn bộ các incident có 'Actions' trong số 5 kết quả, không phải 'cập nhật gần nhất'"; "trong 23 incident khớp vẫn có thể có thêm incident Actions chưa được liệt kê" | A1, H5 | Describe the selection as set membership: "the incidents with <component> in their name among the M returned; N matched in total; the rest were not seen". |
| R17 | "dòng snapshot lẫn từ tiếng Nga 'для'"; "captured_at chưa nhất quán với dữ liệu gốc" | H1 | **Code:** language check — characters from a script not used in the question trigger one rewrite (`validation_foreign_script`). **Prompt:** per-provider captured_at copied exactly. |
| R3b | "'không phải sập toàn cầu' hơi vượt dữ liệu"; "gọi công cụ là 'công cụ tìm kiếm' dù thực tế là tra cứu theo ID"; "'degraded performance' chỉ xuất hiện trong nguồn GitHub … ghép như thể áp dụng cho cả hai" | H3, A8 | "The data does not record that" wording; name the tool by what it did; keep each source's wording with its own source. |

## What v5 (written before this analysis) got wrong

v5's output format still contains "plus the status page of each provider you checked" (cause of R2) and has no
rules for R1 relevance, R5, R6, R7, R8, R10, R11, R12. v6 replaces it with the rules above.

## Code support (not only prompt)

- R1/R6: the evidence ledger marks, for each listed incident, whether the searched keyword appears in its name
  or components, and labels `last_updated` as "not the start time".
- R2: the ledger's "Sources to cite" already holds only URLs returned by tools; the prompt now restricts sources to it.
