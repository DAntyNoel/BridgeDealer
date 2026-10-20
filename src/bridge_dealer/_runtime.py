"""Capture CLI startup before importing the package's native dependencies."""

from time import perf_counter

PROCESS_STARTED = perf_counter()
