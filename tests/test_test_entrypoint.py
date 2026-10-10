from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from features.environment import after_scenario
import run_tests


class TestEntrypointTests(unittest.TestCase):
    def test_all_commands_run_even_if_unit_fails(self):
        with patch.object(run_tests.subprocess, 'run', side_effect=[SimpleNamespace(returncode=1),
                                                                  SimpleNamespace(returncode=0)]) as execute:
            self.assertEqual(1, run_tests.main([]))
        self.assertEqual(2, execute.call_count)
        self.assertIn('behave', execute.call_args.args[0])
        self.assertEqual(run_tests.ROOT, execute.call_args.kwargs['cwd'])
        self.assertEqual('', execute.call_args.kwargs['env']['LOG_FILE'])

    def test_suite_includes_root_tcp_contract(self):
        def ids(suite):
            for item in suite:
                if isinstance(item, unittest.TestSuite):
                    yield from ids(item)
                else:
                    yield item.id()
        self.assertTrue(any(name.startswith('test_tcp_race_contract.') for name in ids(run_tests.unit_suite())))

    def test_cleanup_failure_does_not_skip_other_resources(self):
        good, bad = Mock(), Mock()
        bad.stop.side_effect = OSError('cleanup failed')
        temporary, runtime, bus = Mock(), Mock(), Mock()
        context = SimpleNamespace(resources=[good, bad], temporary=temporary, runtime=runtime, bus=bus)
        scenario = SimpleNamespace(name='already failed', status='failed')
        with self.assertLogs('features.environment', level='ERROR'), self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
            after_scenario(context, scenario)
        good.stop.assert_called_once()
        temporary.cleanup.assert_called_once()
        runtime.close.assert_called_once()
        bus.close.assert_called_once()
        self.assertEqual('failed', scenario.status)

    def test_serial_and_logging_restored_despite_cleanup_error(self):
        import sys
        previous = sys.modules.get('serial')
        handler, logger, bad = Mock(), Mock(), Mock()
        bad.stop.side_effect = OSError('failed')
        context = SimpleNamespace(resources=[bad], serial_module_previous=previous,
            log_handler=handler, core_logger=logger, previous_log_level=20)
        with patch.dict(sys.modules, {'serial': object()}), self.assertLogs('features.environment', level='ERROR'):
            with self.assertRaises(RuntimeError):
                after_scenario(context, SimpleNamespace(name='test'))
            self.assertIs(previous, sys.modules.get('serial'))
        logger.removeHandler.assert_called_once_with(handler)
        handler.close.assert_called_once()
