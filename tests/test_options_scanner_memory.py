"""Offline fake-box tests for the root-only maintenance interface."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'ops' / 'maintenance' / 'options_scanner_memory.py'
spec = importlib.util.spec_from_file_location('afs_options_memory', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FakeSystemctl:
    def __init__(self, start=m.LOW, active='active', high='infinity'):
        self.current = start
        self.active = active
        self.high = high
        self.calls = []
        self.set_to = None
        self.fail_verify = False

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == 'set-property':
            assert args[1:3] == ('--runtime', m.UNIT)
            assert args[3] in ('MemoryMax=600M', 'MemoryMax=350M')
            self.current = m.HIGH if args[3] == 'MemoryMax=600M' else m.LOW
            self.set_to = self.current
            return ''
        if args[0] != 'show':
            raise AssertionError(args)
        current = m.LOW if self.fail_verify and self.set_to == m.HIGH else self.current
        return '\n'.join([f'LoadState=loaded', f'ActiveState={self.active}', f'MemoryMax={current}',
                          f'MemoryHigh={self.high}', 'MemoryCurrent=10000000', 'MemorySwapCurrent=500000000',
                          'ControlGroup=/system.slice/options-scanner.service', 'NRestarts=0'])


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.receipt = self.root / 'receipt.json'
        self.events = []
        self.meminfo = 'MemAvailable: 1250000 kB\n'
        self.caps = [None, None]

    def audit(self, action, status, **kwargs):
        self.events.append((action, status, kwargs))

    def work(self, verb, fake, authorize=lambda x: None):
        return m.execute(verb, runner=fake, authorize=authorize,
                         getprops=m.properties, host_meminfo=self.meminfo,
                         caps=self.caps, receipt=self.receipt, audit=self.audit)

    def test_inspect_is_read_only(self):
        f = FakeSystemctl()
        result = self.work('inspect', f)
        self.assertEqual(result['status'], 'read_only')
        self.assertTrue(all(x[0] == 'show' for x in f.calls))
        self.assertFalse(self.events)

    def test_apply_then_rollback_only_two_allowlisted_values(self):
        f = FakeSystemctl()
        a = self.work('apply-600', f)
        self.assertEqual(a['after'], m.HIGH)
        self.assertTrue(self.receipt.exists())
        b = self.work('rollback-350', f)
        self.assertEqual(b['after'], m.LOW)
        self.assertEqual([x[3] for x in f.calls if x[0] == 'set-property'], ['MemoryMax=600M', 'MemoryMax=350M'])

    def test_no_authorization_no_mutation(self):
        f = FakeSystemctl()
        with self.assertRaises(m.GateError):
            self.work('apply-600', f, authorize=lambda x: (_ for _ in ()).throw(m.GateError('denied')))
        self.assertEqual(f.current, m.LOW)
        self.assertTrue(all(x[0] == 'show' for x in f.calls))

    def test_low_host_memory_denied(self):
        f = FakeSystemctl()
        self.meminfo = 'MemAvailable: 10000 kB\n'
        with self.assertRaises(m.GateError):
            self.work('apply-600', f)
        self.assertEqual(f.current, m.LOW)

    def test_parent_cap_denied(self):
        f = FakeSystemctl()
        self.caps = [None, 660 * m.MIB]
        with self.assertRaises(m.GateError):
            self.work('apply-600', f)
        self.assertEqual(f.current, m.LOW)

    def test_memoryhigh_denied(self):
        f = FakeSystemctl(high=str(400 * m.MIB))
        with self.assertRaises(m.GateError):
            self.work('apply-600', f)
        self.assertEqual(f.current, m.LOW)

    def test_inactive_service_denied(self):
        f = FakeSystemctl(active='inactive')
        with self.assertRaises(m.GateError):
            self.work('apply-600', f)
        self.assertEqual(f.current, m.LOW)

    def test_unexpected_initial_limit_denied(self):
        f = FakeSystemctl(start=400 * m.MIB)
        with self.assertRaises(m.GateError):
            self.work('apply-600', f)
        self.assertEqual(f.current, 400 * m.MIB)

    def test_unexpected_verb_denied(self):
        f = FakeSystemctl()
        with self.assertRaises(m.GateError):
            self.work('restart', f)
        self.assertEqual(f.calls, [])

    def test_rollback_without_receipt_denied(self):
        f = FakeSystemctl(start=m.HIGH)
        with self.assertRaises(m.GateError):
            self.work('rollback-350', f)
        self.assertEqual(f.current, m.HIGH)

    def test_auto_rollback_if_verification_fails(self):
        f = FakeSystemctl()
        f.fail_verify = True
        with self.assertRaises(m.GateError):
            self.work('apply-600', f)
        self.assertEqual(f.current, m.LOW)
        self.assertEqual([c[3] for c in f.calls if c[0] == 'set-property'], ['MemoryMax=600M', 'MemoryMax=350M'])

    def test_approval_exact_scope_and_expiry(self):
        approval = self.root / 'approval.json'
        future = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
        data = {'schema': 1, 'unit': m.UNIT, 'allowed_actions': ['apply-600', 'rollback-350'],
                'expires_utc': future.isoformat()}
        approval.write_text(json.dumps(data))
        m.approved('apply-600', now=dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc), source=approval)
        with self.assertRaises(m.GateError):
            m.approved('apply-600', now=future, source=approval)
        data['unit'] = 'futures-bot.service'
        approval.write_text(json.dumps(data))
        with self.assertRaises(m.GateError):
            m.approved('apply-600', now=dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc), source=approval)
        data['unit'] = m.UNIT
        data['allowed_actions'] = ['apply-600', 'rollback-350', 'restart']
        approval.write_text(json.dumps(data))
        with self.assertRaises(m.GateError):
            m.approved('apply-600', now=dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc), source=approval)

    def test_symlink_approval_rejected(self):
        original = self.root / 'orig.json'
        original.write_text('{}')
        symlink = self.root / 'link.json'
        symlink.symlink_to(original)
        with self.assertRaises(m.GateError):
            m.approved('apply-600', source=symlink)

    def test_parent_cgroup_limits(self):
        cgroot = self.root / 'cgroup'
        (cgroot / 'system.slice').mkdir(parents=True)
        (cgroot / 'cgroup.controllers').write_text('memory')
        (cgroot / 'memory.max').write_text('max')
        (cgroot / 'system.slice' / 'memory.max').write_text(str(1024 * m.MIB))
        self.assertEqual(m.parent_caps('/system.slice/options-scanner.service', cgroot), [None, 1024*m.MIB])
        with self.assertRaises(m.GateError):
            m.parent_caps('/system.slice/../bad.service', cgroot)


if __name__ == '__main__':
    unittest.main()
