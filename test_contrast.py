#!/usr/bin/env python3
"""Compatibility wrapper for the moved contrast smoke test."""


def main():
    from bfg.tools.test_contrast import test_contrast

    return test_contrast()


if __name__ == '__main__':
    raise SystemExit(main())
