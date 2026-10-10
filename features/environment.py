import logging
import sys

log = logging.getLogger(__name__)


def after_scenario(context, scenario):
    actions = [resource.stop for resource in reversed(getattr(context, 'resources', []))]
    runtime = getattr(context, 'runtime', None)
    if runtime is not None:
        actions.append(runtime.close)
    bus = getattr(context, 'bus', None)
    if bus is not None:
        actions.append(bus.close)
    temporary = getattr(context, 'temporary', None)
    if temporary is not None:
        actions.append(temporary.cleanup)
    errors = []
    try:
        for action in actions:
            try:
                action()
            except Exception as exc:
                errors.append(f'{action!r}: {type(exc).__name__}: {exc}')
                log.exception('scenario cleanup failed scenario=%s action=%r', scenario.name, action)
    finally:
        if hasattr(context, 'serial_module_previous'):
            if context.serial_module_previous is None:
                sys.modules.pop('serial', None)
            else:
                sys.modules['serial'] = context.serial_module_previous
        handler = getattr(context, 'log_handler', None)
        if handler is not None:
            context.core_logger.removeHandler(handler)
            handler.close()
            context.core_logger.setLevel(context.previous_log_level)
    if errors:
        # Behave retains the original failed step and reports this as a hook error.
        raise RuntimeError('Scenario cleanup errors: ' + '; '.join(errors))

