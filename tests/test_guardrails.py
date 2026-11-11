"""Tests cho guardrails tự thêm trong agent.py (hybrid ReAct + function calling).

Dùng FakeProvider offline: kiểm tra cơ chế, không đánh giá năng lực model.
"""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import agent
from agent import run_agent
from bonus.tools import BonusTools
from status_tools import StatusTools
from test_lab import FakeProvider, tool_call, final

GITHUB_DETAILS = '{"provider":"github","incident_id":"h02n9572mx9y"}'


class RelatedIncidentTests(unittest.TestCase):
    def test_link_in_update_becomes_next_step_hint(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', GITHUB_DETAILS), final('x https://stspg.io/7pwv28hvkmz8')])
        run_agent('Theo liên kết của incident', fake, StatusTools())
        observed = json.loads(fake.histories[1][-1]['content'])
        self.assertIn({'provider': 'github', 'incident_id': 'xr3myzj7ms51'}, observed['related_incidents_not_read'])

    def test_hint_disappears_once_related_incident_was_read(self):
        a = tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}')
        b = tool_call('2', 'get_incident_details', GITHUB_DETAILS)
        fake = FakeProvider([a, b, final('https://stspg.io/pjgptg3cfc6n https://stspg.io/7pwv28hvkmz8')])
        run_agent('x', fake, StatusTools())
        observed = json.loads(fake.histories[2][-1]['content'])
        self.assertNotIn('related_incidents_not_read', observed)

    def test_tool_result_is_not_mutated(self):
        tools = StatusTools()
        fake = FakeProvider([tool_call('1', 'get_incident_details', GITHUB_DETAILS), final('https://stspg.io/7pwv28hvkmz8')])
        run_agent('x', fake, tools)
        self.assertNotIn('related_incidents_not_read', tools.get_incident_details('github', 'h02n9572mx9y'))


class LoopGuardTests(unittest.TestCase):
    def test_duplicate_successful_call_is_not_executed_again(self):
        call = '{"provider":"github"}'
        fake = FakeProvider([tool_call('1', 'get_status', call), tool_call('2', 'get_status', call), final()])
        result = run_agent('x', fake, StatusTools())
        self.assertEqual([t['event'] for t in result['trace'] if t['event'].startswith('tool')], ['tool', 'tool_duplicate'])
        self.assertEqual(json.loads(fake.histories[2][-1]['content'])['error'], 'DUPLICATE_CALL')

    def test_retry_limit_for_persistent_429(self):
        responses = [tool_call(str(i), 'get_vercel_status', '{}') for i in range(5)] + [final('Vercel không kiểm tra được (429).')]
        result = run_agent('x', FakeProvider(responses), BonusTools(scenario='rate_limit_always'), max_steps=8)
        executed = [t for t in result['trace'] if t['event'] == 'tool' and t['name'] == 'get_vercel_status']
        self.assertEqual(len(executed), agent.MAX_ATTEMPTS)
        self.assertTrue(any(t['event'] == 'tool_blocked' for t in result['trace']))
        self.assertEqual(result['status'], 'completed')

    def test_no_progress_twice_asks_model_to_conclude(self):
        call = '{"provider":"github"}'
        fake = FakeProvider([tool_call('1', 'get_status', call), tool_call('2', 'get_status', call),
                             tool_call('3', 'get_status', call), final()])
        result = run_agent('x', fake, StatusTools(), max_steps=8)
        self.assertTrue(any(t['event'] == 'loop_detected' for t in result['trace']))

    def test_last_step_sends_wrap_up_instruction(self):
        fake = FakeProvider([tool_call('1', 'get_status', '{"provider":"github"}'), final()])
        result = run_agent('x', fake, StatusTools(), max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertIn('[Agent runtime]', fake.histories[1][-1]['content'])
        self.assertEqual(fake.histories[1][-1]['role'], 'system')


class TimeoutAndErrorTests(unittest.TestCase):
    def test_slow_tool_becomes_timeout_observation(self):
        class Slow(StatusTools):
            def execute(self, name, args):
                time.sleep(0.5)
                return super().execute(name, args)
        fake = FakeProvider([tool_call('1', 'get_status', '{"provider":"github"}'), final('không kiểm tra được')])
        with patch.object(agent, 'TOOL_TIMEOUT_SECONDS', 0.05):
            result = run_agent('x', fake, Slow())
        self.assertEqual(json.loads(fake.histories[1][-1]['content'])['error'], 'TOOL_TIMEOUT')
        self.assertEqual(result['status'], 'completed')

    def test_tool_exception_is_observation(self):
        class Broken(StatusTools):
            def execute(self, name, args):
                raise KeyError('boom')
        fake = FakeProvider([tool_call('1', 'get_status', '{"provider":"github"}'), final()])
        self.assertEqual(run_agent('x', fake, Broken())['status'], 'completed')
        self.assertEqual(json.loads(fake.histories[1][-1]['content'])['error'], 'TOOL_EXCEPTION')

    def test_provider_exception_is_error_not_crash(self):
        class Down:
            def complete(self, messages, schemas):
                raise RuntimeError('HTTP 500')
        result = run_agent('x', Down(), StatusTools())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['trace'][-1]['event'], 'provider_error')


class SourceGateTests(unittest.TestCase):
    def test_sources_appended_after_revision_budget(self):
        url = 'https://stspg.io/pjgptg3cfc6n'
        fake = FakeProvider([tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
                             final('a'), final('b'), final('c')])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(url, result['answer'])
        self.assertTrue(any(t['event'] == 'validation_sources_appended' for t in result['trace']))

    def test_each_provider_needs_its_own_source(self):
        a = tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}')
        b = tool_call('2', 'get_incident_details', '{"provider":"cloudflare","incident_id":"yaa0witbl2me"}')
        a['calls'] += b['calls']
        a['message']['tool_calls'] += b['message']['tool_calls']
        fake = FakeProvider([a, final('chỉ có https://stspg.io/pjgptg3cfc6n'), final('done')])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        gate = next(t for t in result['trace'] if t['event'] == 'validation_missing_source')
        self.assertEqual(list(gate['missing']), ['cloudflare'])


class RoleBoundaryTests(unittest.TestCase):
    """Bài Day 3: prompt có cấu trúc, mô tả tool rõ, ranh giới vai trò system/user/tool."""
    def test_prompt_follows_slide_structure(self):
        fake = FakeProvider([final()])
        run_agent('x', fake, StatusTools())
        system = fake.histories[0][0]
        self.assertEqual(system['role'], 'system')
        tags = ['<persona>', '<rules>', '<capabilities>', '<constraints>', '<output_format>', '<escalation>']
        positions = [system['content'].index(t) for t in tags]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(fake.histories[0][1], {'role': 'user', 'content': 'x'})

    def test_tool_descriptions_rewritten_but_contract_unchanged(self):
        from status_tools import SCHEMAS
        class Capture(FakeProvider):
            def complete(self, messages, schemas):
                self.seen = schemas
                return super().complete(messages, schemas)
        fake = Capture([final()])
        run_agent('x', fake, StatusTools())
        for ours, given in zip(fake.seen, SCHEMAS):
            self.assertEqual(ours['function']['name'], given['function']['name'])
            self.assertEqual(ours['function']['parameters'], given['function']['parameters'])
            self.assertIn('DÙNG', ours['function']['description'])
            self.assertIn('KHÔNG', ours['function']['description'])
        self.assertNotIn('DÙNG', SCHEMAS[0]['function']['description'])  # không sửa schema gốc

    def test_tool_output_is_marked_untrusted_and_injection_flagged(self):
        sys.path.insert(0, str(ROOT / 'evals'))
        from scenarios import ScenarioTools
        fake = FakeProvider([tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
                             final('https://stspg.io/pjgptg3cfc6n')])
        run_agent('x', fake, ScenarioTools('injection'))
        observed = json.loads(fake.histories[1][-1]['content'])
        self.assertIn('trust_boundary', observed)
        self.assertTrue(observed['untrusted_instruction_detected'])

    def test_clean_data_is_not_flagged(self):
        fake = FakeProvider([tool_call('1', 'get_status', '{"provider":"github"}'), final()])
        run_agent('x', fake, StatusTools())
        self.assertNotIn('untrusted_instruction_detected', json.loads(fake.histories[1][-1]['content']))

    def test_source_revision_request_is_system_role(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
                             final('thiếu'), final('https://stspg.io/pjgptg3cfc6n')])
        run_agent('x', fake, StatusTools(), max_steps=4)
        self.assertEqual(fake.histories[2][-1]['role'], 'system')
        self.assertTrue(all(m['role'] != 'user' for m in fake.histories[2][2:]))

    def test_bonus_prompt_keeps_provider_context(self):
        fake = FakeProvider([final()])
        run_agent('x', fake, BonusTools())
        self.assertIn('DigitalOcean', fake.histories[0][0]['content'])
        self.assertIn('<constraints>', fake.histories[0][0]['content'])


class HybridTraceTests(unittest.TestCase):
    def test_thought_and_action_recorded_for_tool_steps(self):
        step = tool_call('1', 'get_status', '{"provider":"github"}')
        step['message']['content'] = 'Thought: cần trạng thái GitHub trước.'
        result = run_agent('x', FakeProvider([step, final()]), StatusTools())
        model_event = result['trace'][0]
        self.assertEqual(model_event['thought'], 'Thought: cần trạng thái GitHub trước.')
        self.assertEqual(model_event['actions'][0]['name'], 'get_status')

    def test_trace_has_no_api_key(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-secret-test'}):
            result = run_agent('x', FakeProvider([final()]), StatusTools())
        self.assertNotIn('sk-secret-test', json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
