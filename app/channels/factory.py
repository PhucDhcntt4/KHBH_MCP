from collections.abc import Iterable

from app.channels.base import MessageChannel
from app.channels.telegram_channel import TelegramChannel
from app.channels.zalo_channel import ZaloChannel


def create_channels(
    enabled_channels: Iterable[str],
) -> dict[str, MessageChannel]:
    enabled = {
        channel.strip().lower()
        for channel in enabled_channels
        if channel.strip()
    }
    channels: dict[str, MessageChannel] = {}

    if "telegram" in enabled:
        channels["telegram"] = TelegramChannel()

    if "zalo" in enabled:
        channels["zalo"] = ZaloChannel()

    return channels
