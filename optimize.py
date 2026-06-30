#!/usr/bin/env python3
"""Compatibility wrapper for the moved optimization utility."""


def main():
    from bfg.tools.optimize import main

    return main()


if __name__ == '__main__':
    raise SystemExit(main())
