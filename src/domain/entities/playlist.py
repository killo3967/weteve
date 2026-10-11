from collections.abc import Iterator

from .channel import Channel


class Playlist:
    """Agregado que gestiona una colección de canales."""
    def __init__(self, channels: list[Channel] | None = None):
        self._channels = channels or []

    def add_channel(self, channel: Channel):
        self._channels.append(channel)

    def filter_by_group(self, group_name: str) -> 'Playlist':
        if not group_name:
            return Playlist(list(self._channels))
        filtered = [c for c in self._channels if c.group == group_name]
        return Playlist(filtered)

    def __iter__(self) -> Iterator[Channel]:
        return iter(self._channels)

    def __len__(self) -> int:
        return len(self._channels)

    @property
    def channels(self) -> list[Channel]:
        return list(self._channels)
