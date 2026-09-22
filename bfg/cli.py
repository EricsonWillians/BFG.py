import sys

from PyQt5.QtWidgets import QApplication

from src.runtime import ApplicationRuntime, parse_runtime_options
from src.widgets.main_window import MainWindow


def main():
    options = parse_runtime_options(sys.argv[1:])

    if options.version:
        # Short-circuit: skip full runtime init (config load + source-port
        # discovery) just to print a constant.
        from src.runtime import VERSION
        print(f"BFG.py {VERSION}")
        return 0

    runtime = ApplicationRuntime(options)

    if options.performance_test:
        return runtime.run_performance_test()

    if options.check_config or options.test_config:
        validation = runtime.validate_config()
        runtime._emit_validation(validation)
        return 0 if validation.is_valid else 1

    if options.no_gui:
        return runtime.run_no_gui()

    app = QApplication(sys.argv)
    window = MainWindow(runtime)
    if options.exit_after_launch:
        # Mirror the headless semantics: close the GUI when the game exits.
        runtime.launch_orchestrator.finished.connect(lambda *args: window.close())
    window.show()
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
