"""Contract integration with unmodified official harness; mock reasoning only."""
import asyncio
from pathlib import Path
import shutil
import unittest

from agent.samsung import ParticipantAgent, SamsungProtocol
from agent.kit_media import KitMediaLoader
from agent.multimodal.audio import decode_media, validate_wav
from tests.helpers import ScriptedPlanner
from vendor.samsung_theme05.harness.runner import EvaluationHarness


class HarnessIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unseen_nested_tool_chain_and_write_deduplication(self):
        lookup = {'state_patch': {'intent': 'lookup', 'set_slots': {'item': 'sample'}},
            'tool_requests': [{'tool_name': 'novel_read', 'arguments': {'item': 'sample'}, 'bindings': {'item': 'item'}}]}
        write = {'state_patch': {'set_slots': {'device': {'code': 'abc'}}},
            'tool_requests': [{'tool_name': 'novel_write', 'arguments': {'device': {'code': 'abc'}},
                               'bindings': {'device': 'device'}, 'operation_key': 'one'}]}
        planner = ScriptedPlanner(lookup, write, write)
        agents = []
        def factory(i, o):
            agent = ParticipantAgent(i, o, provider=planner)
            agents.append(agent)
            return agent
        scenario = {'scenario_id': 'integration_only', 'tool_manifest': {
            'novel_read': {'kind': 'read_only', 'description': 'Read an item', 'delay_range_ms': [0, 0],
                'args': {'item': {'type': 'string', 'required': True}}, 'default_result': {'code': 'abc'}},
            'novel_write': {'kind': 'state_modifying', 'description': 'Save the device', 'delay_range_ms': [0, 0],
                'args': {'device': {'type': 'object', 'required': True, 'properties': {'code': {'type': 'string', 'required': True}}}},
                'default_result': {'saved': True}}},
            'events': [{'timestamp_ms': 0, 'event_type': 'user_speech_chunk', 'payload': {'text': 'Read and save the item.', 'end_of_turn': True}}]}
        # This external harness uses wall time; allow startup on a busy CI machine.
        trace = await EvaluationHarness(scenario, factory, tail_ms=1500, verbose=False).run()
        self.assertFalse([e for e in trace if e['kind'] in {'protocol_error', 'agent_crash'}])
        self.assertEqual(len([e for e in trace if e.get('action') == 'tool_call']), 2)
        self.assertEqual(len([e for e in trace if e['kind'] == 'tool_completed']), 2)
        r = agents[0].runtime
        self.assertIn('DUPLICATE_WRITE_BLOCKED', [e.kind for e in r.trace.entries])
        r.trace.save('artifacts/traces/samsung_harness_chain.jsonl')
        self.assertFalse(r.tasks)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is needed for official MP3 media')
    async def test_actual_public_mp3_decodes_without_annotations(self):
        root = Path(__file__).resolve().parents[2] / 'vendor/samsung_theme05'
        protocol = SamsungProtocol('s')
        event = protocol.decode({'timestamp_ms': 0, 'event_type': 'user_audio_chunk',
            'payload': {'audio_ref': 'audio/pub_05_turn1.mp3', 'end_of_turn': True},
            '_reference_text': 'must never enter model input'})
        self.assertNotIn('must never', str(event))
        data = decode_media(await KitMediaLoader(root)(event))
        validate_wav(data)
        self.assertGreater(len(data), 44)
