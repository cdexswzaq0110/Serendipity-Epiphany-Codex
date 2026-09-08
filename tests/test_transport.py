import time
import unittest
from pathlib import Path

from se_codex.transport import run_codex


class Host:
    model = 'gpt-5.6-luna'
    reported_model = model
    usage = {'inputTokens': 20, 'outputTokens': 3, 'cachedInputTokens': 0, 'totalTokens': 23}
    reroute = False

    def __init__(self, root, on_message=None, timeout=20):
        self.observe = on_message
        self.calls = []
        self.sent = False

    def call(self, method, params, timeout=20):
        self.calls.append(method)
        if method == 'model/list':
            return {'data': [{'model': self.model, 'supportedReasoningEfforts': ['low']}]}
        if method == 'config/read':
            return {'config': {'mcp_servers': {'example': {}}, 'apps': {}}}
        if method == 'thread/start':
            assert params['config']['mcp_servers.example.enabled'] is False
            assert params['allowProviderModelFallback'] is False
            assert params['approvalPolicy'] == 'never'
            return {'thread': {'id': 'real-host-thread'}, 'model': self.reported_model, 'reasoningEffort': 'low'}
        if method == 'turn/start':
            return {'turn': {'id': 'real-host-turn'}}
        if method == 'turn/interrupt':
            return {}
        raise AssertionError(method)

    def receive(self, timeout=1):
        if self.sent:
            return
        self.sent = True
        if self.usage is not None:
            self.observe({'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {'total': self.usage}}})
        if self.reroute:
            self.observe({'method': 'model/rerouted', 'params': {'fromModel': self.model, 'toModel': 'unexpected'}})
        self.observe({'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'id': 'reply', 'text': 'done'}}})
        self.observe({'method': 'turn/completed', 'params': {'turn': {'id': 'real-host-turn', 'status': 'completed'}}})

    def close(self):
        pass


class TransportTests(unittest.TestCase):
    def run_host(self, host=Host, **kwargs):
        return run_codex(Path.cwd(), 'Complete task', Host.model, 'low', server_factory=host, **kwargs)

    def test_turn_identity_and_host_evidence_are_retained_separately(self):
        result = self.run_host()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['thread_id'], 'real-host-thread')
        self.assertEqual(result['turn_id'], 'real-host-turn')
        self.assertEqual(result['host_model'], Host.model)
        self.assertIsNone(result['provider_inference_model'])
        self.assertEqual(result['usage']['total_tokens'], 23)

    def test_mismatched_or_rerouted_model_cannot_be_accepted(self):
        class WrongModel(Host):
            reported_model = 'other'
        self.assertEqual(self.run_host(WrongModel)['model_calls'], 0)
        class Rerouted(Host):
            reroute = True
        self.assertEqual(self.run_host(Rerouted)['status'], 'blocked')

    def test_missing_usage_and_final_event_budget_overshoot_block_acceptance(self):
        class NoUsage(Host):
            usage = None
        self.assertEqual(self.run_host(NoUsage)['status'], 'blocked')
        self.assertEqual(self.run_host(max_tokens=1)['status'], 'blocked')

    def test_expired_deadline_never_starts_a_turn(self):
        class SlowDiscovery(Host):
            def call(self, method, params, timeout=20):
                if method == 'model/list':
                    time.sleep(0.02)
                return super().call(method, params, timeout)
        result = self.run_host(SlowDiscovery, seconds=0.01)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['model_calls'], 0)

    def test_server_request_is_recorded_as_intervention(self):
        class Approval(Host):
            def receive(self, timeout=1):
                self.observe({'id': 'approval', 'method': 'item/commandExecution/requestApproval', 'params': {}})
                super().receive(timeout)
        result = self.run_host(Approval)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(len(result['interventions']), 1)
