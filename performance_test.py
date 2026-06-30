#!/usr/bin/env python3
"""Compatibility wrapper for the moved performance test utility."""


def main():
    from bfg.tools.performance_test import run_performance_test

    return run_performance_test()


if __name__ == '__main__':
    raise SystemExit(main())
