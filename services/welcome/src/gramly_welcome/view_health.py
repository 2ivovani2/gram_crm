"""Periodic credential probe and pool threshold notification."""

from __future__ import annotations

import asyncio
import logging

from .view_pool import check_all_accounts


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(check_all_accounts())
