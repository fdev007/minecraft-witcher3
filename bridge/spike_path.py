"""Модули Ведьмака (coords, w3net, fake_w3) живут в ../spike: делаем их импортируемыми. Импортировать первым."""
import os
import sys

SPIKE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spike")
if SPIKE not in sys.path:
    sys.path.insert(0, SPIKE)
