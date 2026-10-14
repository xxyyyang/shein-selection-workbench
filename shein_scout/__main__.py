# -*- coding: utf-8 -*-
"""让包可以直接执行：python -m shein_scout ..."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
