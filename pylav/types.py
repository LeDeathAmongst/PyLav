from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine, Literal, TypeVar, Union

import discord
from discord import app_commands
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from discord.ext.commands import AutoShardedBot, Bot, Cog, Context

    try:
        from redbot.core.bot import Red
        from redbot.core.commands import Cog as RedCog
        from redbot.core.commands import Context as RedContext
    except ImportError:
        Red = AutoShardedBot
        RedCog = Cog
        RedContext = Context

    from pylav.client import Client
    from pylav.query import Query
    from pylav.utils import PyLavContext

else:
    try:
        from redbot.core.bot import Red as BotClient
    except ImportError:
        from discord.ext.commands import AutoShardedBot as BotClient


_Bot = Union["Red", "Bot", "AutoShardedBot"]


class PyLavCogMixin(ABC):
    bot: BotT
    lavalink: Client
    pylav: Client


T = TypeVar("T")
Coro = Coroutine[Any, Any, T]
CoroFunc = Callable[..., Coro[Any]]
MaybeCoro = Union[T, Coro[T]]
MaybeAwaitable = Union[T, Awaitable[T]]

CogT = TypeVar("CogT", bound="Optional[Union[PyLavCogMixin, RedCog, Cog]]")
Check = Callable[["ContextT"], MaybeCoro[bool]]
Hook = Union[Callable[["CogT", "ContextT"], Coro[Any]], Callable[["ContextT"], Coro[Any]]]
Error = Union[
    Callable[["CogT", "ContextT", "CommandError"], Coro[Any]],
    Callable[["ContextT", "CommandError"], Coro[Any]],
]

ContextT = TypeVar("ContextT", bound="Union[PyLavContext[Any], RedContext[Any], Context[Any]]")


class BotClientWithLavalink(BotClient):
    _pylav_client: Client
    lavalink: Client
    pylav: Client
    guild: discord.Guild | None

    async def get_context(
        self, message: discord.abc.Message | InteractionT, *, cls: type[PyLavContext] = None  # noqa: F821
    ) -> PyLavContext[Any]:
        ...


class Interaction(discord.Interaction):
    client: BotClientWithLavalink
    response: discord.InteractionResponse
    followup: discord.Webhook
    command: app_commands.Command[Any, ..., Any] | app_commands.ContextMenu | None
    channel: discord.interactions.InteractionChannel | None


BotT = TypeVar("BotT", bound=BotClientWithLavalink, covariant=True)
InteractionT = TypeVar("InteractionT", bound=Interaction)
QueryT = TypeVar("QueryT", bound="Type[Query]")


class playlistInfoT(TypedDict):  # noqa
    name: str
    selectedTrack: int


class TrackInfoT(TypedDict):
    identifier: str
    isSeekable: bool
    author: str
    length: int
    title: str
    uri: str
    position: int | None
    isStream: bool
    source: str | None


class TrackT(TypedDict):
    track: str
    info: TrackInfoT


class LavalinkResponseT(TypedDict):
    loadType: Literal["TRACK_LOADED", "PLAYLIST_LOADED", "SEARCH_RESULT", "NO_MATCHES", "LOAD_FAILED"]
    playlistInfo: playlistInfoT
    tracks: list[TrackT]


class TimedFeatureT(TypedDict):
    enabled: bool
    time: int
