#!/usr/bin/env python3
"""Compatibility wrapper for the moved layout smoke test."""


def main():
    from bfg.tools.test_layout import test_layout

    return test_layout()


if __name__ == '__main__':
    raise SystemExit(main())
