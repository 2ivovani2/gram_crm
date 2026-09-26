"""Prepare a Telethon .session file locally for upload in hello-admin."""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path

from telethon import TelegramClient  # type: ignore[import-not-found,import-untyped]


async def authorize(output: Path) -> None:
    if output.suffix != ".session" or output.exists():
        raise ValueError("Choose a new path ending in .session")
    api_id = int(input("Telegram API ID: ").strip())
    api_hash = getpass.getpass("Telegram API hash: ").strip()
    if not api_hash or not api_hash.isalnum():
        raise ValueError("Invalid Telegram API hash")
    os.umask(0o077)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = TelegramClient(str(output), api_id, api_hash)
    try:
        await client.start(
            phone=lambda: input("Phone (international format): ").strip(),
            code_callback=lambda: getpass.getpass("Telegram login code: ").strip(),
            password=lambda: getpass.getpass("Two-step password, if requested: "),
        )
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram account was not authorized")
    finally:
        await client.disconnect()
    os.chmod(output, 0o600)
    print(f"Session file ready: {output}. Upload it in hello-admin; never commit or share it in chat.")


def run() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: welcome-view-authorize /secure/path/account.session")
    asyncio.run(authorize(Path(sys.argv[1]).expanduser().resolve()))
