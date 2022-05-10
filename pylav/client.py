from __future__ import annotations

import asyncio
import contextlib
import datetime
import itertools
import operator
import pathlib
import random
from types import MethodType
from typing import AsyncIterator, Callable, Iterator

import aiohttp
import aiohttp_client_cache
import aiopath
import discord
import ujson
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from asyncspotify import Client as SpotifyClient
from asyncspotify import ClientCredentialsFlow
from discord.abc import Messageable
from discord.ext.commands import Context
from discord.types.embed import EmbedType

from pylav._config import __VERSION__, CONFIG_DIR
from pylav._logging import getLogger
from pylav.dispatcher import DispatchManager
from pylav.events import Event
from pylav.exceptions import AnotherClientAlreadyRegistered, NoNodeAvailable, NoNodeWithRequestFunctionalityAvailable
from pylav.m3u8_parser import M3U8Parser
from pylav.managed_node import LocalNodeManager
from pylav.node import Node
from pylav.node_manager import NodeManager
from pylav.player import Player
from pylav.player_manager import PlayerManager
from pylav.query import MAX_RECURSION_DEPTH, Query
from pylav.radio import RadioBrowser
from pylav.sql.clients.lib import LibConfigManager
from pylav.sql.clients.nodes_db_manager import NodeConfigManager
from pylav.sql.clients.player_config_manager import PlayerConfigManager
from pylav.sql.clients.player_state_db_manager import PlayerStateDBManager
from pylav.sql.clients.playlist_manager import PlaylistConfigManager
from pylav.sql.clients.query_manager import QueryCacheManager
from pylav.sql.clients.updater import UpdateSchemaManager
from pylav.tracks import Track
from pylav.types import BotT, CogT, ContextT, InteractionT, LavalinkResponseT
from pylav.utils import PyLavContext, SingletonMethods, _get_context, _process_commands, _Singleton, add_property

LOGGER = getLogger("PyLav.Client")

_COGS_REGISTERED = set()

_OLD_PROCESS_COMMAND_METHOD: Callable = None  # type: ignore
_OLD_GET_CONTEXT: Callable = None  # type: ignore


class Client(metaclass=_Singleton):
    """
    Represents a Lavalink client used to manage nodes and connections.

    .. _event loop: https://docs.python.org/3/library/asyncio-eventloop.html

    Parameters
    ----------
    bot : :class:`discord.Client`
        The bot instance.
    player: Optional[:class:`Player`]
        The class that should be used for the player. Defaults to ``Player``.
        Do not change this unless you know what you are doing!
    connect_back: Optional[:class:`bool`]
        A boolean that determines if a player will connect back to the
        node it was originally connected to. This is not recommended doing since
        the player will most likely be performing better in the new node. Defaults to `False`.

        Warning
        -------
        If this option is enabled and the player's node is changed through `Player.change_node` after
        the player was moved via the fail-over mechanism, the player will still move back to the original
        node when it becomes available. This behaviour can be avoided in custom player implementations by
        setting `self._original_node` to `None` in the `change_node` function.
    """

    _local_node_manager: LocalNodeManager

    _asyncio_lock = asyncio.Lock()
    _initiated = False

    def __init__(
        self,
        bot: BotT,
        cog: CogT,
        player: type[Player] = Player,  # type: ignore
        connect_back: bool = False,
        config_folder: aiopath.AsyncPath | pathlib.Path = CONFIG_DIR,
    ):
        try:
            global _COGS_REGISTERED, _OLD_PROCESS_COMMAND_METHOD, _OLD_GET_CONTEXT
            setattr(bot, "_pylav_client", self)
            add_property(bot, "lavalink", lambda b: b._pylav_client)
            if _OLD_PROCESS_COMMAND_METHOD is None:
                _OLD_PROCESS_COMMAND_METHOD = bot.process_commands
            if _OLD_GET_CONTEXT is None:
                _OLD_GET_CONTEXT = bot.get_context
            bot.process_commands = MethodType(_process_commands, bot)
            bot.get_context = MethodType(_get_context, bot)
            _COGS_REGISTERED.add(cog.__cog_name__)
            self._config_folder = aiopath.AsyncPath(config_folder)
            self._bot = bot
            self._user_id = str(bot.user.id)
            self._aiohttp_client_cache = aiohttp_client_cache.SQLiteBackend(
                cache_name=str(config_folder / ".cache" / "aiohttp-requests.db"),
                cache_control=True,
                allowed_codes=(200,),
                allowed_methods=("GET",),
                ignored_parameters=["timestamp"],
                include_headers=True,
                expire_after=datetime.timedelta(days=1),
            )
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), json_serialize=ujson.dumps)
            self._cached_session = aiohttp_client_cache.CachedSession(
                timeout=aiohttp.ClientTimeout(total=30), json_serialize=ujson.dumps, cache=self._aiohttp_client_cache
            )
            self._node_manager = NodeManager(self)
            self._player_manager = PlayerManager(self, player)
            self._lib_config_manager = LibConfigManager(self)
            self._node_config_manager = NodeConfigManager(self)
            self._playlist_config_manager = PlaylistConfigManager(self)
            self._query_cache_manager = QueryCacheManager(self)
            self._update_schema_manager = UpdateSchemaManager(self)
            self._dispatch_manager = DispatchManager(self)
            self._player_state_db_manager = PlayerStateDBManager(self)
            self._player_config_manager = PlayerConfigManager(self)
            self._radio_manager = RadioBrowser(self)
            self._m3u8parser = M3U8Parser(self)
            self._connect_back = connect_back
            self._warned_about_no_search_nodes = False
            self._spotify_client_id = None
            self._spotify_client_secret = None
            self._spotify_auth = None
            self._shutting_down = False
            self.enable_managed_node = None
            self._scheduler = AsyncIOScheduler()
        except Exception:
            LOGGER.exception("Failed to initialize Lavalink")
            raise

    @property
    def initialized(self) -> bool:
        """Returns whether the client has been initialized."""
        return self._initiated

    @property
    def scheduler(self) -> AsyncIOScheduler:
        """Returns the scheduler."""
        return self._scheduler

    @property
    def radio_browser(self) -> RadioBrowser:
        """Returns the radio browser instance."""
        return self._radio_manager

    @property
    def player_config_manager(self) -> PlayerConfigManager:
        """Returns the player config manager."""
        return self._player_config_manager

    @property
    def spotify_client(self) -> SpotifyClient:
        """Returns the spotify client."""
        return SpotifyClient(self._spotify_auth)

    @property
    def is_shutting_down(self) -> bool:
        """Returns whether the client is shutting down."""
        return self._shutting_down

    @property
    def node_db_manager(self) -> NodeConfigManager:
        """Returns the sql node config manager."""
        return self._node_config_manager

    @property
    def player_state_db_manager(self) -> PlayerStateDBManager:
        return self._player_state_db_manager

    @property
    def playlist_db_manager(self) -> PlaylistConfigManager:
        """Returns the sql playlist config manager."""
        return self._playlist_config_manager

    @property
    def lib_db_manager(self) -> LibConfigManager:
        """Returns the sql lib config manager."""
        return self._lib_config_manager

    @property
    def query_cache_manager(self) -> QueryCacheManager:
        """Returns the query cache manager."""
        return self._query_cache_manager

    @property
    def managed_node_controller(self) -> LocalNodeManager:
        return self._local_node_manager

    @property
    def node_manager(self) -> NodeManager:
        return self._node_manager

    @property
    def player_manager(self) -> PlayerManager:
        return self._player_manager

    @property
    def config_folder(self) -> aiopath.AsyncPath:
        return self._config_folder

    @property
    def bot(self) -> BotT:
        return self._bot

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session

    @property
    def cached_session(self) -> aiohttp_client_cache.CachedSession:
        return self._cached_session

    @property
    def lib_version(self) -> str:
        return __VERSION__

    @property
    def bot_id(self) -> str:
        return self._user_id

    @SingletonMethods.run_once_async
    async def initialize(
        self,
    ) -> None:
        try:
            if not self._initiated:
                async with self._asyncio_lock:
                    if not self._initiated:
                        self._initiated = True
                        await self.bot.wait_until_ready()
                        if hasattr(self.bot, "get_shared_api_token"):
                            spotify = await self.bot.get_shared_api_tokens("spotify")
                            client_id = spotify.get("client_id")
                            client_secret = spotify.get("client_secret")
                        else:
                            client_id = None
                            client_secret = None
                        await self._lib_config_manager.initialize()
                        await self._update_schema_manager.run_updates()
                        await self._radio_manager.initialize()
                        await self._player_manager.initialize()

                        config_data = await self._lib_config_manager.get_config(
                            config_folder=self._config_folder,
                            java_path="java",
                            enable_managed_node=True,
                            auto_update_managed_nodes=True,
                            localtrack_folder=self._config_folder / "music",
                        )
                        auto_update_managed_nodes = config_data.auto_update_managed_nodes
                        self.enable_managed_node = config_data.enable_managed_node
                        self._config_folder = aiopath.AsyncPath(config_data.config_folder)
                        localtrack_folder = aiopath.AsyncPath(config_data.localtrack_folder)
                        data = await self._node_config_manager.get_bundled_node_config()
                        if not all([client_id, client_secret]):
                            spotify_data = data.yaml["plugins"]["topissourcemanagers"]["spotify"]
                            client_id = spotify_data["clientId"]
                            client_secret = spotify_data["clientSecret"]
                        elif all([client_id, client_secret]):
                            if (
                                data.yaml["plugins"]["topissourcemanagers"]["spotify"]["clientId"] != client_id
                                or data.yaml["plugins"]["topissourcemanagers"]["spotify"]["clientSecret"]
                                != client_secret
                            ):
                                data.yaml["plugins"]["topissourcemanagers"]["spotify"]["clientId"] = client_id
                                data.yaml["plugins"]["topissourcemanagers"]["spotify"]["clientSecret"] = client_secret
                            await data.save()
                        self._spotify_client_id = client_id
                        self._spotify_client_secret = client_secret
                        self._spotify_auth = ClientCredentialsFlow(
                            client_id=self._spotify_client_id, client_secret=self._spotify_client_secret
                        )
                        from pylav.localfiles import LocalFile

                        await LocalFile.add_root_folder(path=localtrack_folder, create=True)
                        self._user_id = str(self._bot.user.id)
                        self._local_node_manager = LocalNodeManager(self, auto_update=auto_update_managed_nodes)
                        if self.enable_managed_node:
                            await self._local_node_manager.start(java_path=config_data.java_path)
                        await self.playlist_db_manager.update_bundled_playlists()
                        await self.node_manager.connect_to_all_nodes()
                        await self.node_manager.wait_until_ready()
                        await self.player_manager.restore_player_states()
                        self._scheduler.add_job(
                            self._query_cache_manager.delete_old,
                            trigger="interval",
                            seconds=600,
                            max_instances=1,
                        )
                        self._scheduler.start()
        except Exception as exc:
            LOGGER.critical("Failed start up", exc_info=exc)
            raise exc

    async def register(self, cog: CogT) -> None:
        global _COGS_REGISTERED
        LOGGER.info("Registering cog %s", cog.__cog_name__)
        if (instance := getattr(self.bot, "lavalink", None)) and not isinstance(instance, Client):
            raise AnotherClientAlreadyRegistered(
                f"Another client instance has already been registered to bot.lavalink with type: {type(instance)}"
            )
        _COGS_REGISTERED.add(cog.__cog_name__)

    async def update_spotify_tokens(self, client_id: str, client_secret: str) -> None:
        self._spotify_client_id = client_id
        self._spotify_client_secret = client_secret
        self._spotify_auth = ClientCredentialsFlow(
            client_id=self._spotify_client_id, client_secret=self._spotify_client_secret
        )
        bundled_node_config = await self._node_config_manager.get_bundled_node_config()
        bundled_node_config.extras["plugins"]["topissourcemanagers"]["spotify"]["clientId"] = client_id
        bundled_node_config.extras["plugins"]["topissourcemanagers"]["spotify"]["clientSecret"] = client_secret
        await bundled_node_config.save()

    async def add_node(
        self,
        *,
        unique_identifier: int,
        host: str,
        port: int,
        password: str,
        resume_key: str = None,
        resume_timeout: int = 60,
        name: str = None,
        reconnect_attempts: int = -1,
        ssl: bool = False,
        search_only: bool = False,
        managed: bool = False,
        skip_db: bool = False,
        yaml: dict | None = None,
        disabled_sources: list[str] = None,
        extras: dict = None,
    ) -> Node:
        """
        Adds a node to Lavalink's node manager.

        Parameters
        ----------
        host: :class:`str`
            The address of the Lavalink node.
        port: :class:`int`
            The port to use for websocket and REST connections.
        password: :class:`str`
            The password used for authentication.
        resume_key: Optional[:class:`str`]
            A resume key used for resuming a session upon re-establishing a WebSocket connection to Lavalink.
            Defaults to `None`.
        resume_timeout: Optional[:class:`int`]
            How long the node should wait for a connection while disconnected before clearing all players.
            Defaults to `60`.
        name: :class:`str`
            An identifier for the node that will show in logs. Defaults to `None`
        reconnect_attempts: Optional[:class:`int`]
            The amount of times connection with the node will be reattempted before giving up.
            Set to `-1` for infinite. Defaults to `3`.
        ssl: Optional[:class:`bool`]
            Whether to use SSL for the connection. Defaults to `False`.
        search_only: :class:`bool`
            Whether the node should only be used for searching. Defaults to `False`.
        unique_identifier: :class:`in`
            A unique identifier for the node. Defaults to `None`.
        skip_db: :class:`bool`
            Whether the node should skip the database. Defaults to `False`.
        yaml: Optional[:class:`dict`]
            A dictionary of extra information to be stored in the node. Defaults to `None`.
        extras: Optional[:class:`dict`]
            A dictionary of extra information to be stored in the node. Defaults to `None`.
        managed: :class:`bool`
            Whether the node is managed by the client. Defaults to `False`.
        disabled_sources: Optional[:class:`list`[:class:`str`]]
            A list of sources that should be disabled for the node. Defaults to `None`.
        """
        return await self.node_manager.add_node(
            host=host,
            port=port,
            password=password,
            resume_key=resume_key,
            resume_timeout=resume_timeout,
            name=name,
            reconnect_attempts=reconnect_attempts,
            ssl=ssl,
            search_only=search_only,
            unique_identifier=unique_identifier,
            skip_db=skip_db,
            managed=managed,
            yaml=yaml,
            disabled_sources=disabled_sources,
            extras=extras or {},
        )

    async def decode_track(self, track: str, feature: str = None) -> dict | None:
        """|coro|
        Decodes a base64-encoded track string into a dict.

        Parameters
        ----------
        track: :class:`str`
            The base64-encoded `track` string.
        feature: Optional[:class:`str`]
            The feature to decode the track for. Defaults to `None`.

        Returns
        -------
        :class:`dict`
            A dict representing the track's information.
        """
        if not self.node_manager.available_nodes:
            raise NoNodeAvailable("No available nodes!")
        node = self.node_manager.find_best_node(feature=feature)
        if node is None and feature:
            raise NoNodeWithRequestFunctionalityAvailable(
                f"No node with {feature} functionality available!", feature=feature
            )
        return await node.decode_track(track)

    async def decode_tracks(self, tracks: list, feature: str = None) -> list[dict]:
        """|coro|
        Decodes a list of base64-encoded track strings into a dict.

        Parameters
        ----------
        tracks: list[:class:`str`]
            A list of base64-encoded `track` strings.
        feature: Optional[:class:`str`]
            The feature to decode the tracks for. Defaults to `None`.

        Returns
        -------
        List[:class:`dict`]
            A list of dicts representing track information.
        """
        if not self.node_manager.available_nodes:
            raise NoNodeAvailable("No available nodes!")
        node = self.node_manager.find_best_node(feature=feature)
        if node is None and feature:
            raise NoNodeWithRequestFunctionalityAvailable(
                f"No node with {feature} functionality available!", feature=feature
            )
        return await node.decode_tracks(tracks)

    @staticmethod
    async def routeplanner_status(node: Node) -> dict | None:
        """|coro|
        Gets the route-planner status of the target node.

        Parameters
        ----------
        node: :class:`Node`
            The node to use for the query.

        Returns
        -------
        :class:`dict`
            A dict representing the route-planner information.
        """
        return await node.routeplanner_status()

    @staticmethod
    async def routeplanner_free_address(node: Node, address: str) -> bool:
        """|coro|
        Gets the route-planner status of the target node.

        Parameters
        ----------
        node: :class:`Node`
            The node to use for the query.
        address: :class:`str`
            The address to free.

        Returns
        -------
        :class:`bool`
            True if the address was freed, False otherwise.
        """
        return await node.routeplanner_free_address(address)

    @staticmethod
    async def routeplanner_free_all_failing(node: Node) -> bool:
        """|coro|
        Gets the route-planner status of the target node.

        Parameters
        ----------
        node: :class:`Node`
            The node to use for the query.

        Returns
        -------
        :class:`bool`
            True if all failing addresses were freed, False otherwise.
        """
        return await node.routeplanner_free_all_failing()

    def dispatch_event(self, event: Event):
        asyncio.create_task(self._dispatch_event(event))

    async def _dispatch_event(self, event: Event):
        """|coro|
        Dispatches the given event to all registered hooks.

        Parameters
        ----------
        event: :class:`Event`
            The event to dispatch to the hooks.
        """
        event_dispatcher = [self._dispatch_manager.dispatch]

        task_list = []
        for hook in itertools.chain(
            event_dispatcher,
        ):
            task = asyncio.create_task(hook(event))  # type: ignore
            task.set_name(f"Event hook {hook.__name__}")
            task.add_done_callback(self.__done_callback)
            task_list.append(task)
        await asyncio.gather(*task_list)

    @staticmethod
    def __done_callback(task: asyncio.Task):
        exc = task.exception()
        if exc is not None:
            name = task.get_name()
            LOGGER.warning("Event hook %s encountered an exception!", name)
            LOGGER.debug("Event hook %s encountered an exception!", name, exc_info=exc)

    async def unregister(self, cog: discord.ext.commands.Cog):
        """|coro|
        Unregister the specified Cog and if no cogs are left closes the client.

        Parameters
        ----------
        cog: :class:`discord.ext.commands.Cog`
            The cog to unregister.
        """
        global _COGS_REGISTERED
        if not self._shutting_down:
            async with self._asyncio_lock:
                if not self._shutting_down:
                    _COGS_REGISTERED.discard(cog.__cog_name__)
                    LOGGER.info("%s has been unregistered", cog.__cog_name__)
                    if not _COGS_REGISTERED:
                        self._shutting_down = True
                        try:
                            Client._instances.clear()
                            SingletonMethods.reset()
                            self._initiated = False
                            await self.player_manager.save_all_players()
                            await self.player_manager.shutdown()
                            await self._node_manager.close()
                            await self._local_node_manager.shutdown()
                            await self._session.close()
                            await self._cached_session.close()
                            self._scheduler.shutdown(wait=True)
                        except Exception as e:
                            LOGGER.critical("Failed to shutdown the client", exc_info=e)
                        if _OLD_PROCESS_COMMAND_METHOD is not None:
                            self.bot.process_commands = _OLD_PROCESS_COMMAND_METHOD
                        if _OLD_GET_CONTEXT is not None:
                            self.bot.get_context = _OLD_GET_CONTEXT
                        del self.bot._pylav_client  # noqa
                        LOGGER.info("All cogs have been unregistered, PyLav client has been shutdown.")

    def get_player(self, guild: discord.Guild | int | None) -> Player | None:
        """|coro|
        Gets the player for the target guild.

        Parameters
        ----------
        guild: :class:`discord.Guild`
            The guild to get the player for.

        Returns
        -------
        :class:`Player`
            The player for the target guild.
        """
        if not guild:
            return None
        if not isinstance(guild, int):
            guild = guild.id
        return self.player_manager.get(guild)

    async def connect_player(
        self,
        requester: discord.Member,
        channel: discord.VoiceChannel,
        node: Node = None,
        self_deaf: bool = True,
    ) -> Player:
        """|coro|
        Connects the player for the target guild.

        Parameters
        ----------
        channel: :class:`discord.VoiceChannel`
            The channel to connect to.
        node: :class:`Node`
            The node to use for the connection.
        self_deaf: :class:`bool`
            Whether the bot should be deafened.
        requester: :class:`discord.Member`
            The member requesting the connection.
        Returns
        -------
        :class:`Player`
            The player for the target guild.
        """
        p = await self.player_manager.create(channel, channel.rtc_region, node, self_deaf, requester)
        return p

    async def construct_embed(
        self,
        *,
        embed: discord.Embed = None,
        colour: discord.Colour | int | None = None,
        color: discord.Colour | int | None = None,
        title: str = None,
        type: EmbedType = "rich",
        url: str = None,
        description: str = None,
        timestamp: datetime.datetime = None,
        author_name: str = None,
        author_url: str = None,
        thumbnail: str = None,
        footer: str = None,
        footer_url: str = None,
        messageable: Messageable | InteractionT = None,
    ) -> discord.Embed:

        if messageable and not colour and not color and hasattr(self._bot, "get_embed_color"):
            colour = await self._bot.get_embed_color(messageable)
        elif colour or color:
            colour = colour or color
        if timestamp and isinstance(timestamp, datetime.datetime):
            timestamp = timestamp
        else:
            timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
        contents = dict(
            title=title,
            type=type,
            url=url,
            description=description,
            timestamp=timestamp.isoformat(),
        )
        embed = embed.to_dict() if embed is not None else {}
        contents |= embed
        new_embed = discord.Embed.from_dict(contents)
        new_embed.color = colour

        if footer:
            new_embed.set_footer(text=footer, icon_url=footer_url)
        if thumbnail:
            new_embed.set_thumbnail(url=thumbnail)
        if author_url and author_name:
            new_embed.set_author(name=author_name, icon_url=author_url)
        return new_embed

    async def get_context(self, what: discord.Message | ContextT | InteractionT) -> PyLavContext:
        if isinstance(what, PyLavContext):
            return what
        elif isinstance(what, Context):
            ctx_ = what.interaction or what.message
            ctx: PyLavContext = await self._bot.get_context(ctx_, cls=PyLavContext)  # type: ignore

        else:
            ctx: PyLavContext = await self._bot.get_context(what, cls=PyLavContext)  # type: ignore
        return ctx

    async def update_localtracks_folder(self, folder: str | None) -> None:
        from pylav.localfiles import LocalFile

        localtrack_folder = aiopath.AsyncPath(folder) if folder else self._config_folder / "music"

        await LocalFile.add_root_folder(path=localtrack_folder, create=True)

    async def get_all_players(self) -> Iterator[Player]:

        return iter(self.player_manager)

    async def get_managed_node(self) -> Node | None:
        available_nodes = list(filter(operator.attrgetter("available"), self.node_manager.managed_nodes))

        if not available_nodes:
            return None
        return random.choice(available_nodes)

    async def _get_tracks(
        self,
        query: Query,
        first: bool = False,
        bypass_cache: bool = False,
    ) -> dict:
        """|coro|
        Gets all tracks associated with the given query.

        Parameters
        ----------
        query: :class:`Query`
            The query to perform a search for
        first: Optional[:class:`bool`]
            Whether to only return the first track. Defaults to `False`.
        bypass_cache: Optional[:class:`bool`]
            Whether to bypass the cache. Defaults to `False`.

        Returns
        -------
        :class:`dict`
            A dict representing tracks.
        """
        if not self.node_manager.available_nodes:
            raise NoNodeAvailable("No available nodes!")
        node = self.node_manager.find_best_node(feature=query.requires_capability)
        if node is None:
            raise NoNodeWithRequestFunctionalityAvailable(
                f"No node with {query.requires_capability} functionality available!", query.requires_capability
            )
        return await node.get_tracks(query, first=first, bypass_cache=bypass_cache)

    async def get_all_tracks_for_queries(
        self,
        *queries: Query,
        requester: discord.Member,
        player: Player | None = None,
        bypass_cache: bool = False,
        enqueue: bool = True,
    ) -> tuple[list[Track], int, list[Query]]:  # sourcery no-metrics
        """High level interface to get and return all tracks for a list of queries.

        This will automatically handle playlists, albums, searches and local files.

        Parameters
        ----------
        queries : `Query`
            The list of queries to search for.
        bypass_cache : `bool`, optional
            Whether to bypass the cache and force a new search.
            Local files will always be bypassed.
        requester : `discord.Nember`
            The user who requested the op.
        player : `Player`
            The player requesting the op.
        enqueue : `bool`, optional
            Whether to enqueue the tracks as needed
            while try are processed so users dont sit waiting for the bot to finish.

        Returns
        -------
        tracks : `List[AudioTrack]`
            The list of tracks found.
        total_tracks : `int`
            The total number of tracks found.
        queries : `List[Query]`
            The list of queries that were not found.

        """
        successful_tracks = []
        queries_failed = []
        track_count = 0

        for query in queries:
            node = self.node_manager.find_best_node(feature=query.requires_capability)
            if node is None:
                queries_failed.append(query)
            # Query tracks as the queue builds as this may be a slow operation
            if enqueue and successful_tracks and not player.is_playing and not player.paused:
                track = successful_tracks.pop()
                await player.play(track, await track.query(), requester)
            if query.is_search or query.is_single:
                try:
                    track = await self._get_tracks(query=query, first=True, bypass_cache=bypass_cache)
                    track_b64 = track.get("track")
                    if not track_b64:
                        queries_failed.append(query)
                    if track_b64:
                        track_count += 1
                        successful_tracks.append(
                            Track(
                                data=track_b64,
                                node=node,
                                query=await Query.from_base64(track_b64),
                                requester=requester.id,
                            )
                        )
                except NoNodeWithRequestFunctionalityAvailable:
                    queries_failed.append(query)
            elif (query.is_playlist or query.is_album) and not query.is_local and not query.is_custom_playlist:
                try:
                    tracks: dict = await self._get_tracks(query=query, bypass_cache=bypass_cache)
                    track_list = tracks.get("tracks", [])
                    if not track_list:
                        queries_failed.append(query)
                    for track in track_list:
                        if track_b64 := track.get("track"):
                            track_count += 1
                            successful_tracks.append(
                                Track(
                                    data=track_b64,
                                    node=node,
                                    query=await Query.from_base64(track_b64),
                                    requester=requester.id,
                                )
                            )
                            # Query tracks as the queue builds as this may be a slow operation
                            if enqueue and successful_tracks and not player.is_playing and not player.paused:
                                track = successful_tracks.pop()
                                await player.play(track, await track.query(), requester)
                except NoNodeWithRequestFunctionalityAvailable:
                    queries_failed.append(query)
            elif (query.is_local or query.is_custom_playlist) and query.is_album:
                try:
                    yielded = False
                    async for local_track in query.get_all_tracks_in_folder():
                        yielded = True
                        track = await self._get_tracks(query=local_track, first=True, bypass_cache=True)
                        if track_b64 := track.get("track"):
                            track_count += 1
                            successful_tracks.append(
                                Track(
                                    data=track_b64,
                                    node=node,
                                    query=await Query.from_base64(track_b64),
                                    requester=requester.id,
                                )
                            )
                            # Query tracks as the queue builds as this may be a slow operation
                            if enqueue and successful_tracks and not player.is_playing and not player.paused:
                                track = successful_tracks.pop()
                                await player.play(track, await track.query(), requester)
                    if not yielded:
                        queries_failed.append(query)
                except NoNodeWithRequestFunctionalityAvailable:
                    queries_failed.append(query)
            else:
                queries_failed.append(query)
                LOGGER.warning("Unhandled query: %s, %s", query.__dict__, query.query_identifier)
        return successful_tracks, track_count, queries_failed

    @staticmethod
    async def _yield_recursive_queries(query: Query, recursion_depth: int = 0) -> AsyncIterator[Query]:
        """|coro|
        Gets all queries associated with the given query.
        Parameters
        ----------
        query: :class:`Query`
            The query to perform a search for
        """
        if query.invalid or recursion_depth > MAX_RECURSION_DEPTH:
            return
        recursion_depth += 1
        if query.is_m3u:
            async for m3u in query._yield_m3u_tracks():
                with contextlib.suppress(Exception):
                    async for q in query._yield_tracks_recursively(m3u, recursion_depth):
                        LOGGER.warning("Yielding m3u..2. tracks: %s", q.__dict__)
                        yield q
        elif query.is_pylav:
            async for pylav in query._yield_pylav_file_tracks():
                with contextlib.suppress(Exception):
                    async for q in query._yield_tracks_recursively(pylav, recursion_depth):
                        yield q
        elif query.is_pls:
            async for pls in query._yield_pls_tracks():
                with contextlib.suppress(Exception):
                    async for q in query._yield_tracks_recursively(pls, recursion_depth):
                        yield q
        elif query.is_local and query.is_album:
            async for local in query._yield_local_tracks():
                yield local
        else:
            yield query

    async def get_tracks(
        self,
        *queries: Query,
        bypass_cache: bool = False,
    ) -> LavalinkResponseT:
        """This method can be rather slow as it recursibly queries all queries and their associated entries.

        Thus if you are processing user input  you may be interested in using
        the :meth:`get_all_tracks_for_queries` where it can enqueue tracks as needed to the player.


        Parameters
        ----------
        queries : `Query`
            The list of queries to search for.
        bypass_cache : `bool`, optional
            Whether to bypass the cache and force a new search.
            Local files will always be bypassed.
        """
        output_tracks = []
        playlist_name = ""
        for query in queries:
            async for response in self._yield_recursive_queries(query):
                with contextlib.suppress(NoNodeWithRequestFunctionalityAvailable):
                    node = self.node_manager.find_best_node(feature=response.requires_capability)
                    if node is None or response.is_custom_playlist:
                        continue
                    if response.is_playlist or response.is_album:
                        _response = await node.get_tracks(response, bypass_cache=bypass_cache)
                        playlist_name = _response.get("playlistInfo", {}).get("name", "")
                        output_tracks.extend(_response["tracks"])
                    elif response.is_single:
                        _response = await node.get_tracks(response, first=True, bypass_cache=bypass_cache)
                        output_tracks.append(_response)
                    else:
                        LOGGER.critical("Unknown query type: %s", response)

        return {
            "playlistInfo": {
                "name": playlist_name if len(queries) == 1 else "",
                "selectedTrack": -1,
            },
            "loadType": "SEARCH_RESULT" if output_tracks else "LOAD_FAILED",
            "tracks": output_tracks,
        }

    async def remove_node(self, node_id: int):
        """Removes a node from the node manager."""
        node = self.node_manager.get_node_by_id(node_id)
        await self.node_manager.remove_node(node)
