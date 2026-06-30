import sys

from PyQt5.QtWidgets import QApplication

from src.runtime import ApplicationRuntime, parse_runtime_options
from src.widgets.main_window import MainWindow


def main():
    options = parse_runtime_options(sys.argv[1:])
    runtime = ApplicationRuntime(options)

    if options.version:
        print(f"BFG.py {runtime.print_version()}")
        return 0

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
    window.show()
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
