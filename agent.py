"""Status Investigation Agent: vòng ReAct tự viết (không dùng framework), dạng hybrid.

Hybrid = ReAct + native function calling (FC):
  - Action do model chọn bằng FC (tool name + JSON arguments đúng schema), không parse text.
  - Model ghi kèm 1 câu "Thought" ngắn trong content của cùng message; trace lưu
    Thought → Action → Observation cho từng step để debug như ReAct.

Một step = một lần gọi model. Trong mỗi step:
  1. gọi provider.complete(messages, schemas)
  2. append assistant message nguyên vẹn vào messages
  3. nếu có tool calls: thực thi TẤT CẢ, trả từng kết quả kèm đúng tool_call_id, rồi sang step sau
  4. nếu finish == 'stop' và không có calls: kiểm tra nguồn rồi trả câu trả lời

Prompt và tool (slide Day 3 e403): system prompt Persona → Rules → Capabilities → Constraints → Output
format → Escalation; mô tả tool nói khi nào dùng/không dùng; ranh giới vai trò: chỉ dẫn runtime đi bằng
vai trò system, kết quả tool gắn trust_boundary và được coi là dữ liệu.

Guardrails (slide Day 4 "Max Iterations Safeguard"):
  - max_steps (số lần gọi model) + bước cuối buộc trả lời bằng dữ liệu đã có
  - timeout cho từng tool call
  - retry có kiểm soát: một call lỗi chỉ được thử lại tối đa MAX_ATTEMPTS lần; 429 đọc retry-after
  - phát hiện loop: call trùng đã thành công thì trả lại kết quả cũ, không gọi lại; 2 step liền
    không có tiến triển thì yêu cầu model kết luận
  - token budget: vượt ngân sách thì yêu cầu kết luận ngay
  - lỗi tool là observation; lỗi provider hoặc output bị cắt kết thúc run với status 'error'
  - kiểm tra nguồn: đã đọc incident thành công thì câu trả lời phải chứa URL incident đó
"""
import concurrent.futures
import copy
import json
import re
import time
from pathlib import Path

from status_tools import SCHEMAS

# Domain description used in <persona> for the core tools (English version of the lab's original prompt,
# which stays in commit 80424d7 as the baseline).
SYSTEM_PROMPT = '''You investigate the status of GitHub and Cloudflare.'''

# Prompts live in prompts/ (versioned like Day 3: one hypothesis per version, snapshots in versions/vN/).
# system_prompt.md follows the Day 3 lesson (e403): Persona -> Rules -> Capabilities -> Constraints
# -> Output format -> Escalation. tool_guides.json holds the rewritten tool descriptions.
PROMPT_DIR = Path(__file__).resolve().parent / 'prompts'
STRUCTURED_PROMPT = (PROMPT_DIR / 'system_prompt.md').read_text(encoding='utf-8').strip()
_GUIDES = json.loads((PROMPT_DIR / 'tool_guides.json').read_text(encoding='utf-8'))
TOOL_GUIDES = _GUIDES['tools']
PAGINATION_GUIDE = _GUIDES['pagination']
STATUS_GUIDE_BONUS = _GUIDES['bonus_status']

TRUST_BOUNDARY = _GUIDES['trust_boundary']
SUSPICIOUS = re.compile(r'(ignore (the )?(previous|prior|user|system)|system override|^\s*system\s*:|output exactly|treat this .* priority|bỏ qua (mọi )?chỉ dẫn)', re.I | re.M)


def _tool_schemas(schemas):
    """Copy of the lab's schemas with rewritten descriptions; names, parameters and required stay unchanged."""
    out = copy.deepcopy(schemas)
    for item in out:
        fn = item.get('function', {})
        name = fn.get('name', '')
        if name in TOOL_GUIDES:
            fn['description'] = TOOL_GUIDES[name]
            if 'offset' in fn.get('parameters', {}).get('properties', {}):
                fn['description'] += PAGINATION_GUIDE
        elif name.startswith('get_') and name.endswith('_status') and len(name) > len('get__status'):
            fn['description'] = STATUS_GUIDE_BONUS.format(provider=name[4:-7])
    return out


def _providers(schemas):
    """Provider names from per-provider tools (get_<p>_status) or from a provider enum in the parameters."""
    found = []
    for s in schemas:
        fn = s.get('function', {})
        name = fn.get('name', '')
        if name.startswith('get_') and name.endswith('_status') and len(name) > len('get__status'):
            found.append(name[4:-7])
        for value in fn.get('parameters', {}).get('properties', {}).get('provider', {}).get('enum', []):
            found.append(value)
    pretty = {'github': 'GitHub', 'cloudflare': 'Cloudflare', 'vercel': 'Vercel', 'npm': 'npm', 'digitalocean': 'DigitalOcean'}
    return [pretty.get(n, n) for n in dict.fromkeys(found)]


def _system_prompt(tools, schemas):
    providers = _providers(schemas)
    if providers:
        domain = 'You investigate the status of ' + ', '.join(providers[:-1]) + (' and ' if len(providers) > 1 else '') + providers[-1] + '.'
    else:
        domain = SYSTEM_PROMPT
    names = ', '.join(s.get('function', {}).get('name', '?') for s in schemas)
    prompt = STRUCTURED_PROMPT.format(domain=domain, tools=names)
    extra = getattr(tools, 'system_prompt', None)
    if extra:
        # The bonus tool set ships its own (Vietnamese) prompt; the GUIDE asks agents to use it, so it is
        # appended verbatim as tool-set notes rather than translated.
        prompt += '\n\n<tool_set_notes>\n' + extra + '\n</tool_set_notes>'
    return prompt


def _when(ts):
    return ts or 'unknown'


def _evidence_ledger(trace):
    """Facts computed by code from executed tool calls. Rebuilt before every model call and placed in the
    system prompt, so the final answer is written under a guided system prompt that already states what was
    read, what was only listed, what failed and which limits may be claimed. Only allow-listed fields are
    copied, so unexpected fields in tool results (for example injected notices) never reach this block."""
    status, read, listed, failed, not_found, limits, sources = {}, {}, {}, {}, [], [], []
    for t in trace:
        if t.get('event') not in ('tool', 'tool_blocked'):
            continue
        name, args, r = t.get('name') or '', t.get('arguments') or {}, t.get('result') or {}
        provider = r.get('provider') or (args.get('provider') if isinstance(args, dict) else None) \
            or (name[4:-7] if name.startswith('get_') and name.endswith('_status') and len(name) > 11 else '?')
        if r.get('ok') is False or 'error' in r:
            if r.get('error') == 'INCIDENT_NOT_FOUND':
                not_found.append(f"{args.get('incident_id')} ({provider}): INCIDENT_NOT_FOUND in the incidents searched")
            else:
                # One line per distinct call with its attempt count, so a single failure is never read as a retry.
                key = f"{name}({json.dumps(args, ensure_ascii=False)})"
                n = failed.get(key, (0, ''))[0] + 1
                failed[key] = (n, r.get('error'))
            continue
        failed.pop(f"{name}({json.dumps(args, ensure_ascii=False)})", None)
        if 'overall' in r:
            overall = r.get('overall') or {}
            line = (f"{provider}: captured_at={_when(r.get('captured_at'))}; overall indicator={overall.get('indicator')} "
                    f"\"{overall.get('description')}\"")
            comps = ', '.join(f"{c.get('name')}={c.get('status')}" for c in r.get('components') or [])
            if comps:
                line += f'; components: {comps}'
            query = args.get('component') if isinstance(args, dict) else None
            total = r.get('matching_incidents_in_last_50', r.get('match_count'))
            line += f"; keyword search {json.dumps(query or '', ensure_ascii=False)}: {total} matching, {len(r.get('incidents') or [])} returned"
            status.setdefault(provider, []).append(line)
            for i in r.get('incidents') or []:
                # R1: relevance is only "verified" when the searched keyword is in the incident name; the tool also
                # matches update text, so other hits are "returned by the search, relevance not verified".
                if query:
                    hit = query.casefold() in (i.get('name') or '').casefold()
                    rel = '; name matches' if hit else '; name does not match (relevance unverified)'
                else:
                    rel = ''
                listed.setdefault(i.get('id'), f"{i.get('id')} ({provider}) \"{i.get('name')}\" status={i.get('status')} last_updated={_when(i.get('updated_at'))}{rel}")
            if r.get('note'):
                limits.append(f"{provider} search: {r['note']}")
            sources.append(r.get('source') or '')
        elif r.get('url') and r.get('id'):
            updates = r.get('updates') or []
            total = r.get('updates_total', r.get('total_updates'))
            truncated = sum(1 for u in updates if u.get('truncated'))
            line = (f"{r.get('id')} ({provider}) \"{r.get('name')}\" status={r.get('status')} created_at={_when(r.get('created_at'))} "
                    f"last_updated={_when(r.get('updated_at'))} components={r.get('components') or []} "
                    f"updates {len(updates)}/{total}")
            if r.get('next_offset') is not None:
                line += f" next_offset={r['next_offset']}"
            line += f' url={r["url"]}'
            read[r.get('id')] = line
            if isinstance(total, int) and total > len(updates) and r.get('next_offset') is None and 'offset' not in r:
                limits.append(f"{r.get('id')}: only {len(updates)} of {total} updates received")
            if r.get('next_offset') is not None:
                limits.append(f"{r.get('id')}: updates after offset {r['next_offset']} not read")
            if truncated:
                limits.append(f"{r.get('id')}: {truncated} update(s) truncated")
            if r.get('untrusted_instruction_detected'):
                limits.append(f"{r.get('id')}: instruction-like text ignored")
            sources.append(r['url'])
    if not (status or read or failed or not_found):
        return ''
    out = ['<evidence_ledger>', 'Computed by the agent runtime from tool results (data). Base the answer on it.']
    if status:
        out.append('Status:')
        out += [f'- {x}' for lines in status.values() for x in dict.fromkeys(lines)]
    if read:
        out.append('Incidents READ:')
        out += [f'- {x}' for x in read.values()]
    only_listed = [v for k, v in listed.items() if k not in read]
    if only_listed:
        out.append('Incidents only LISTED (not read):')
        out += [f'- {x}' for x in only_listed]
    if failed:
        out.append('Could NOT be checked:')
        out += [f'- {k} -> {err} (failed attempts: {n} of max {MAX_ATTEMPTS})' for k, (n, err) in failed.items()]
    if not_found:
        out.append('Not found:')
        out += [f'- {x}' for x in not_found]
    out.append('Limits:')
    out += [f'- {x}' for x in dict.fromkeys(limits)] or ['- none recorded']
    out.append('Sources: ' + (', '.join(dict.fromkeys(x for x in sources if x)) or 'none'))
    out.append('</evidence_ledger>')
    return '\n'.join(out)


NOTE_LINE = re.compile(r'^\s*(Plan|Thought)\s*:', re.I)


def _split_notes(answer):
    """Hybrid trace: internal Plan/Thought lines belong in the trace, not in the user-facing answer.
    Leading note lines (and their bullet continuations) are moved out of the final answer."""
    lines = answer.splitlines()
    notes, i = [], 0
    while i < len(lines) and (NOTE_LINE.match(lines[i]) or (notes and lines[i].strip().startswith('-')) or (notes and not lines[i].strip())):
        notes.append(lines[i])
        i += 1
    if not notes:
        return answer, ''
    return '\n'.join(lines[i:]).strip(), '\n'.join(n for n in notes if n.strip())


def _mark_untrusted(result):
    """Add a trust boundary to the observation and flag instruction-like text found in the data."""
    if not SUSPICIOUS.search(json.dumps(result, ensure_ascii=False, default=str)):
        return result
    marked = dict(result)
    marked['untrusted_instruction_detected'] = True
    marked['trust_boundary'] = TRUST_BOUNDARY
    return marked


PREVIEW_CHARS = 300
TOOL_TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3            # tối đa số lần chạy cùng một call (gồm lần đầu) khi call đó lỗi
MAX_RETRY_WAIT_SECONDS = 5  # chỉ chờ retry-after ngắn; dài hơn thì báo model thay vì ngủ
TOKEN_BUDGET = 120_000      # tổng token (prompt + completion) cho một run
MAX_SOURCE_REVISIONS = 2

INCIDENT_LINK = re.compile(r'https?://[^\s)\]"\']+/incidents/([a-z0-9]{8,16})', re.I)
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix='tool')


def _preview(text, limit=PREVIEW_CHARS):
    if not text:
        return ''
    return text if len(text) <= limit else text[:limit] + '…'


def _parse_arguments(raw):
    """Trả (args, error). Model có thể sinh JSON hỏng hoặc không phải object."""
    if isinstance(raw, dict):
        return raw, None
    try:
        args = json.loads(raw) if raw not in (None, '') else {}
    except (TypeError, ValueError) as exc:
        return None, f'Invalid JSON: {str(exc)[:120]}'
    if not isinstance(args, dict):
        return None, 'Arguments must be a JSON object.'
    return args, None


def _execute(tools, name, args, timeout=None):
    """Chạy tool với timeout; mọi lỗi đều thành observation, không làm crash vòng lặp."""
    timeout = TOOL_TIMEOUT_SECONDS if timeout is None else timeout
    future = _POOL.submit(tools.execute, name, args)
    try:
        result = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return {'ok': False, 'error': 'TOOL_TIMEOUT', 'message': f'The tool returned nothing within {timeout}s; this part could not be checked.'}
    except Exception as exc:  # tool tự viết có thể ném lỗi ngoài dự kiến
        return {'ok': False, 'error': 'TOOL_EXCEPTION', 'message': f'{type(exc).__name__}: {str(exc)[:200]}'}
    if not isinstance(result, dict):
        return {'ok': False, 'error': 'INVALID_TOOL_RESULT', 'message': 'The tool did not return a JSON object.'}
    return result


def _failed(result):
    return result.get('ok') is False or 'error' in result


def _retry_wait(result):
    """Số giây cần chờ trước khi retry 429, hoặc None nếu không nên chờ."""
    if result.get('error') != 'RATE_LIMITED':
        return None
    raw = result.get('retry_after_seconds', result.get('retry_after'))
    try:
        seconds = float(raw) if raw is not None else 1.0
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= MAX_RETRY_WAIT_SECONDS else None


def _related_incidents(result, provider, read_ids):
    """Incident được nhắc trong update nhưng chưa đọc: gợi ý bước tiếp theo cho model."""
    found = []
    for update in result.get('updates') or []:
        for incident_id in INCIDENT_LINK.findall(update.get('body') or ''):
            if incident_id != result.get('id') and incident_id not in read_ids and incident_id not in found:
                found.append(incident_id)
    return [{'provider': provider, 'incident_id': i} for i in found]


def _total_tokens(usage):
    total = 0
    for u in usage:
        if not isinstance(u, dict):
            continue
        if isinstance(u.get('total_tokens'), int):
            total += u['total_tokens']
        else:
            total += sum(v for k, v in u.items() if k in ('prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens') and isinstance(v, int))
    return total


def run_agent(question, provider, tools, max_steps=6):
    """Trả dict: status, answer, trace, usage. Contract chi tiết trong GUIDE.md."""
    schemas = _tool_schemas(getattr(tools, 'schemas', SCHEMAS))
    base_system = _system_prompt(tools, schemas)
    messages = [{'role': 'system', 'content': base_system},
                {'role': 'user', 'content': question}]
    ledger = ''
    trace, usage = [], []
    attempts = {}       # signature -> số lần đã chạy thật
    succeeded = {}      # signature -> result thành công (để chặn call trùng)
    sources = {}        # provider -> set URL incident đã đọc thành công
    read_ids = set()
    revisions = 0
    no_progress = 0
    wrap_up_sent = False

    def done(status, answer='', error=None):
        out = {'status': status, 'answer': answer or '', 'trace': trace, 'usage': usage,
               'model_calls': len(usage), 'total_tokens': _total_tokens(usage), 'evidence_ledger': ledger}
        if error:
            out['error'] = error
        return out

    def note(text, event, step):
        # Ranh giới vai trò: chỉ dẫn của runtime đi bằng vai trò system, không giả làm người dùng.
        messages.append({'role': 'system', 'content': f'[Agent runtime] {text}'})
        trace.append({'event': event, 'step': step, 'message': text})

    for step in range(1, max_steps + 1):
        if not wrap_up_sent and (step == max_steps or _total_tokens(usage) > TOKEN_BUDGET) and step > 1:
            reason = 'this is the last model call' if step == max_steps else 'the token budget is exceeded'
            note(f'Stop investigating ({reason}). Answer now with the data you already have and call no more tools; '
                 'say clearly what could not be checked and cite the source URLs you read.', 'wrap_up', step)
            wrap_up_sent = True

        # Guided final answer: the system prompt carries a code-built evidence ledger of everything observed.
        new_ledger = _evidence_ledger(trace)
        if new_ledger != ledger:
            ledger = new_ledger
            messages[0] = {'role': 'system', 'content': base_system + '\n\n' + ledger}
        try:
            response = provider.complete(messages, schemas)
        except Exception as exc:
            error = f'{type(exc).__name__}: {str(exc)[:200]}'
            trace.append({'event': 'provider_error', 'step': step, 'error': error})
            return done('error', error=error)

        message = response.get('message') or {'role': 'assistant', 'content': None}
        calls = response.get('calls') or []
        finish = response.get('finish')
        usage.append(response.get('usage') or {})
        trace.append({'event': 'model', 'step': step, 'finish': finish,
                      'thought': _preview(message.get('content'), 800) if calls else '',
                      'actions': [{'name': c.get('name'), 'arguments': c.get('arguments')} for c in calls],
                      'content_preview': '' if calls else _preview(message.get('content'))})
        messages.append(message)

        if finish == 'length':
            # Output bị cắt: kể cả tool call cũng có thể thiếu arguments, không coi là xong.
            trace.append({'event': 'error', 'step': step, 'error': 'MODEL_OUTPUT_TRUNCATED'})
            return done('error', message.get('content') or '', 'Model output was cut off (finish=length).')

        if calls:
            progressed = False
            for call in calls:
                name, raw = call.get('name'), call.get('arguments')
                args, parse_error = _parse_arguments(raw)
                event = 'tool'
                if parse_error:
                    result = {'ok': False, 'error': 'INVALID_ARGUMENTS', 'message': parse_error}
                else:
                    signature = name + ':' + json.dumps(args, sort_keys=True, ensure_ascii=False)
                    if signature in succeeded:
                        # Loop guard: không chạy lại call đã thành công.
                        event = 'tool_duplicate'
                        result = {'ok': False, 'error': 'DUPLICATE_CALL',
                                  'message': 'This exact call already succeeded in an earlier step; use that result instead of calling again.'}
                    elif attempts.get(signature, 0) >= MAX_ATTEMPTS:
                        event = 'tool_blocked'
                        result = {'ok': False, 'error': 'RETRY_LIMIT_REACHED',
                                  'message': f'Tried {MAX_ATTEMPTS} times and every attempt failed; stop retrying and report this part as not checked.'}
                    else:
                        attempts[signature] = attempts.get(signature, 0) + 1
                        result = _execute(tools, name, args)
                        wait = _retry_wait(result)
                        if wait is not None and attempts[signature] < MAX_ATTEMPTS:
                            time.sleep(wait)
                            trace.append({'event': 'retry_wait', 'step': step, 'name': name, 'seconds': wait})
                        if not _failed(result):
                            result = _mark_untrusted(result)
                            progressed = True
                            succeeded[signature] = True
                            if name == 'get_incident_details' and result.get('url'):
                                prov = result.get('provider') or args.get('provider')
                                sources.setdefault(prov, set()).add(result['url'])
                                read_ids.add(result.get('id'))
                                related = _related_incidents(result, prov, read_ids)
                                if related:
                                    result['related_incidents_not_read'] = related
                        else:
                            progressed = True  # lỗi mới vẫn là thông tin mới cho model
                            # Make the retry budget explicit on the observation itself.
                            result = dict(result, attempt=attempts[signature], retries_left=MAX_ATTEMPTS - attempts[signature])
                trace.append({'event': event, 'step': step, 'name': name,
                              'arguments': args if args is not None else raw, 'result': result})
                messages.append({'role': 'tool', 'tool_call_id': call.get('id'),
                                 'content': json.dumps(result, ensure_ascii=False, default=str)})
            no_progress = 0 if progressed else no_progress + 1
            if no_progress >= 2 and not wrap_up_sent:
                note('Two steps in a row produced no new data (duplicate or blocked calls). Conclude with the data you have.',
                     'loop_detected', step)
                wrap_up_sent = True
            continue

        if finish != 'stop':
            trace.append({'event': 'error', 'step': step, 'error': f'UNEXPECTED_FINISH:{finish}'})
            return done('error', message.get('content') or '', f'Model stopped unexpectedly (finish={finish}).')

        answer, final_notes = _split_notes(message.get('content') or '')
        if final_notes:
            trace.append({'event': 'final_thought', 'step': step, 'thought': _preview(final_notes, 800)})
        # Evidence gate: mỗi provider đã đọc incident thành công phải có ít nhất 1 URL incident trong answer.
        missing = {p: sorted(urls) for p, urls in sources.items() if not any(u in answer for u in urls)}
        if missing:
            if revisions < MAX_SOURCE_REVISIONS:
                revisions += 1
                wanted = ', '.join(u for urls in missing.values() for u in urls)
                trace.append({'event': 'validation_missing_source', 'step': step, 'missing': missing})
                messages.append({'role': 'system', 'content': '[Agent runtime] The answer is missing the source URL of an incident you read. '
                                 f'Rewrite the answer, keep its content and cite these exact URLs: {wanted}. Call no more tools.'})
                continue
            # Hết lượt sửa: gắn nguồn bằng code thay vì trả câu trả lời thiếu nguồn.
            listed = '\n'.join(f'- {u}' for urls in missing.values() for u in urls)
            answer = f'{answer}\n\nSources (incidents read):\n{listed}'
            trace.append({'event': 'validation_sources_appended', 'step': step, 'missing': missing})
        trace.append({'event': 'final', 'step': step, 'answer_chars': len(answer)})
        return done('completed', answer)

    trace.append({'event': 'max_steps', 'step': max_steps})
    return done('max_steps', error=f'Used all {max_steps} model calls before reaching an answer.')
