def after_scenario(context, scenario):
    for resource in reversed(getattr(context, "resources", [])):
        try:
            resource.stop()
        except Exception:
            pass
    temporary = getattr(context, "temporary", None)
    if temporary is not None:
        temporary.cleanup()
    if hasattr(context, "serial_module_previous"):
        import sys
        if context.serial_module_previous is None:
            sys.modules.pop("serial", None)
        else:
            sys.modules["serial"] = context.serial_module_previous

