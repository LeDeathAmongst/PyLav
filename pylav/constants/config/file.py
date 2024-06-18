from __future__ import annotations

import base64
import os
from copy import deepcopy
from typing import Any, cast

import yaml
from deepdiff import DeepDiff  # type: ignore

# noinspection PyProtectedMember
from pylav._internals.functions import _get_path, fix
from pylav.constants.config import ENV_FILE
from pylav.constants.config.utils import _remove_keys
from pylav.constants.node_features import SUPPORTED_SEARCHES
from pylav.constants.specials import _MAPPING
from pylav.constants.specials import ANIME as _ANIME
from pylav.logging import getLogger

LOGGER = getLogger("PyLav.Environment")

data = cast(dict[str, Any], yaml.safe_load(ENV_FILE.open(mode="r").read()))
data_new = deepcopy(data)

if (POSTGRES_PORT := data.get("PYLAV__POSTGRES_PORT")) is None:
    # noinspection SpellCheckingInspection
    POSTGRES_PORT = os.getenv("PYLAV__POSTGRES_PORT", os.getenv("PGPORT"))
    data_new["PYLAV__POSTGRES_PORT"] = POSTGRES_PORT

if (POSTGRES_PASSWORD := data.get("PYLAV__POSTGRES_PASSWORD")) is None:
    # noinspection SpellCheckingInspection
    POSTGRES_PASSWORD = os.getenv("PYLAV__POSTGRES_PASSWORD", os.getenv("PGPASSWORD"))
    data_new["PYLAV__POSTGRES_PASSWORD"] = POSTGRES_PASSWORD

if (POSTGRES_USER := data.get("PYLAV__POSTGRES_USER")) is None:
    # noinspection SpellCheckingInspection
    POSTGRES_USER = os.getenv("PYLAV__POSTGRES_USER", os.getenv("PGUSER"))
    data_new["PYLAV__POSTGRES_USER"] = POSTGRES_USER

if (POSTGRES_DATABASE := data.get("PYLAV__POSTGRES_DB")) is None:
    # noinspection SpellCheckingInspection
    POSTGRES_DATABASE = os.getenv("PYLAV__POSTGRES_DB", os.getenv("PGDATABASE"))
    data_new["PYLAV__POSTGRES_DB"] = POSTGRES_DATABASE

if (POSTGRES_HOST := data.get("PYLAV__POSTGRES_HOST")) is None:
    POSTGRES_HOST = os.getenv("PYLAV__POSTGRES_HOST", os.getenv("PGHOST"))
    data_new["PYLAV__POSTGRES_HOST"] = POSTGRES_HOST

if (POSTGRES_SOCKET := data.get("PYLAV__POSTGRES_SOCKET")) is None:
    POSTGRES_SOCKET = os.getenv("PYLAV__POSTGRES_SOCKET")
    data_new["PYLAV__POSTGRES_SOCKET"] = POSTGRES_SOCKET

if (POSTGRES_CONNECTIONS := data.get("PYLAV__POSTGRES_CONNECTIONS")) is None:
    POSTGRES_CONNECTIONS = int(os.getenv("PYLAV__POSTGRES_CONNECTIONS", "100"))
    data_new["PYLAV__POSTGRES_CONNECTIONS"] = POSTGRES_CONNECTIONS
FALLBACK_POSTGREST_HOST = POSTGRES_HOST
if POSTGRES_SOCKET is not None:
    POSTGRES_PORT = None
    POSTGRES_HOST = POSTGRES_SOCKET

if (REDIS_FULL_ADDRESS_RESPONSE_CACHE := data.get("PYLAV__REDIS_FULL_ADDRESS_RESPONSE_CACHE")) is None:
    REDIS_FULL_ADDRESS_RESPONSE_CACHE = os.getenv("PYLAV__REDIS_FULL_ADDRESS_RESPONSE_CACHE")
    data_new["PYLAV__REDIS_FULL_ADDRESS_RESPONSE_CACHE"] = REDIS_FULL_ADDRESS_RESPONSE_CACHE

if (READ_CACHING_ENABLED := data.get("PYLAV__READ_CACHING_ENABLED")) is None:
    READ_CACHING_ENABLED = bool(int(os.getenv("PYLAV__READ_CACHING_ENABLED", "0")))
    data_new["PYLAV__READ_CACHING_ENABLED"] = READ_CACHING_ENABLED
if (JAVA_EXECUTABLE := data.get("PYLAV__JAVA_EXECUTABLE")) is None:
    JAVA_EXECUTABLE = _get_path(os.getenv("PYLAV__JAVA_EXECUTABLE") or "java")
    data_new["PYLAV__JAVA_EXECUTABLE"] = JAVA_EXECUTABLE

if (EXTERNAL_UNMANAGED_HOST := data.get("PYLAV__EXTERNAL_UNMANAGED_HOST")) is None:
    EXTERNAL_UNMANAGED_HOST = os.getenv("PYLAV__EXTERNAL_UNMANAGED_HOST")
    data_new["PYLAV__EXTERNAL_UNMANAGED_HOST"] = EXTERNAL_UNMANAGED_HOST

if (EXTERNAL_UNMANAGED_PORT := data.get("PYLAV__EXTERNAL_UNMANAGED_PORT")) is None:
    EXTERNAL_UNMANAGED_PORT = int(os.getenv("PYLAV__EXTERNAL_UNMANAGED_PORT", "80"))
    data_new["PYLAV__EXTERNAL_UNMANAGED_PORT"] = EXTERNAL_UNMANAGED_PORT

if (EXTERNAL_UNMANAGED_PASSWORD := data.get("PYLAV__EXTERNAL_UNMANAGED_PASSWORD")) is None:
    EXTERNAL_UNMANAGED_PASSWORD = os.getenv("PYLAV__EXTERNAL_UNMANAGED_PASSWORD")
    data_new["PYLAV__EXTERNAL_UNMANAGED_PASSWORD"] = EXTERNAL_UNMANAGED_PASSWORD

if (EXTERNAL_UNMANAGED_SSL := data.get("PYLAV__EXTERNAL_UNMANAGED_SSL")) is None:
    EXTERNAL_UNMANAGED_SSL = bool(int(os.getenv("PYLAV__EXTERNAL_UNMANAGED_SSL", "0")))
    data_new["PYLAV__EXTERNAL_UNMANAGED_SSL"] = EXTERNAL_UNMANAGED_SSL

if (EXTERNAL_UNMANAGED_NAME := data.get("PYLAV__EXTERNAL_UNMANAGED_NAME")) is None:
    EXTERNAL_UNMANAGED_NAME = os.getenv("PYLAV__EXTERNAL_UNMANAGED_NAME") or "ENVAR Node (Unmanaged)"
    data_new["PYLAV__EXTERNAL_UNMANAGED_NAME"] = EXTERNAL_UNMANAGED_NAME

if (TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS := data.get("PYLAV__TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS")) is None:
    TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS = max(
        int(os.getenv("PYLAV__TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS", "1")), 1
    )
    data_new["PYLAV__TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS"] = TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS

if (
    TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS := data.get(
        "PYLAV__TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS"
    )
) is None:
    TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS = max(
        int(os.getenv("PYLAV__TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS", "7")), 7
    )
    data_new["PYLAV__TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS"] = (
        TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS
    )

if (TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS := data.get("PYLAV__TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS")) is None:
    TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS = max(
        int(os.getenv("PYLAV__TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS", "7")), 7
    )
    data_new["PYLAV__TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS"] = TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS

if (DEFAULT_SEARCH_SOURCE := data.get("PYLAV__DEFAULT_SEARCH_SOURCE")) is None:
    DEFAULT_SEARCH_SOURCE = os.getenv("PYLAV__DEFAULT_SEARCH_SOURCE")

if DEFAULT_SEARCH_SOURCE not in SUPPORTED_SEARCHES:
    # noinspection SpellCheckingInspection
    LOGGER.warning("Invalid search source %s, defaulting to dzsearch", DEFAULT_SEARCH_SOURCE)
    LOGGER.info("Valid search sources are %s", ", ".join(SUPPORTED_SEARCHES.keys()))
    # noinspection SpellCheckingInspection
    DEFAULT_SEARCH_SOURCE = "dzsearch"
data_new["PYLAV__DEFAULT_SEARCH_SOURCE"] = DEFAULT_SEARCH_SOURCE

if (MANAGED_NODE_SPOTIFY_CLIENT_ID := data.get("PYLAV__MANAGED_NODE_SPOTIFY_CLIENT_ID")) is None:
    MANAGED_NODE_SPOTIFY_CLIENT_ID = os.getenv("PYLAV__MANAGED_NODE_SPOTIFY_CLIENT_ID", "")
    data_new["PYLAV__MANAGED_NODE_SPOTIFY_CLIENT_ID"] = MANAGED_NODE_SPOTIFY_CLIENT_ID

if (MANAGED_NODE_SPOTIFY_CLIENT_SECRET := data.get("PYLAV__MANAGED_NODE_SPOTIFY_CLIENT_SECRET")) is None:
    MANAGED_NODE_SPOTIFY_CLIENT_SECRET = os.getenv("PYLAV__MANAGED_NODE_SPOTIFY_CLIENT_SECRET") or ""
    data_new["PYLAV__MANAGED_NODE_SPOTIFY_CLIENT_SECRET"] = MANAGED_NODE_SPOTIFY_CLIENT_SECRET

if (MANAGED_NODE_SPOTIFY_COUNTRY_CODE := data.get("PYLAV__MANAGED_NODE_SPOTIFY_COUNTRY_CODE")) is None:
    MANAGED_NODE_SPOTIFY_COUNTRY_CODE = os.getenv("PYLAV__MANAGED_NODE_SPOTIFY_COUNTRY_CODE") or "US"
    data_new["PYLAV__MANAGED_NODE_SPOTIFY_COUNTRY_CODE"] = MANAGED_NODE_SPOTIFY_COUNTRY_CODE

if (MANAGED_NODE_APPLE_MUSIC_API_KEY := data.get("PYLAV__MANAGED_NODE_APPLE_MUSIC_API_KEY")) is None:
    MANAGED_NODE_APPLE_MUSIC_API_KEY = os.getenv("PYLAV__MANAGED_NODE_APPLE_MUSIC_API_KEY") or ""
    data_new["PYLAV__MANAGED_NODE_APPLE_MUSIC_API_KEY"] = MANAGED_NODE_APPLE_MUSIC_API_KEY

if (MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE := data.get("PYLAV__MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE")) is None:
    MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE = os.getenv("PYLAV__MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE") or "US"
    data_new["PYLAV__MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE"] = MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE

if (MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN := data.get("PYLAV__MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN")) is None:
    MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN = os.getenv("PYLAV__MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN") or ""
    data_new["PYLAV__MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN"] = MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN

if (MANAGED_NODE_DEEZER_KEY := data.get("PYLAV__MANAGED_NODE_DEEZER_KEY")) is None:
    MANAGED_NODE_DEEZER_KEY = os.getenv("PYLAV__MANAGED_NODE_DEEZER_KEY")
MANAGED_NODE_DEEZER_KEY = MANAGED_NODE_DEEZER_KEY or _ANIME
if MANAGED_NODE_DEEZER_KEY and MANAGED_NODE_DEEZER_KEY.startswith("id"):
    _temp = [MANAGED_NODE_DEEZER_KEY[i : i + 16] for i in range(0, len(MANAGED_NODE_DEEZER_KEY), 16)]
    MANAGED_NODE_DEEZER_KEY = "".join(
        [
            base64.b64decode(r).decode()
            for r in [
                fix(_temp[2], _MAPPING[2]),
                fix(_temp[1], _MAPPING[1]),
                fix(_temp[3], _MAPPING[3]),
                fix(_temp[0], _MAPPING[0]),
            ]
        ]
    )
data_new["PYLAV__MANAGED_NODE_DEEZER_KEY"] = MANAGED_NODE_DEEZER_KEY

if (LOCAL_TRACKS_FOLDER := data.get("PYLAV__LOCAL_TRACKS_FOLDER")) is None:
    LOCAL_TRACKS_FOLDER = os.getenv("PYLAV__LOCAL_TRACKS_FOLDER")
    data_new["PYLAV__LOCAL_TRACKS_FOLDER"] = LOCAL_TRACKS_FOLDER

if (DATA_FOLDER := data.get("PYLAV__DATA_FOLDER")) is None:
    DATA_FOLDER = os.getenv("PYLAV__DATA_FOLDER")
    data_new["PYLAV__DATA_FOLDER"] = DATA_FOLDER

if (ENABLE_NODE_RESUMING := data.get("PYLAV__ENABLE_NODE_RESUMING")) is None:
    ENABLE_NODE_RESUMING = bool(int(os.getenv("PYLAV__ENABLE_NODE_RESUMING", "1")))
    data_new["PYLAV__ENABLE_NODE_RESUMING"] = ENABLE_NODE_RESUMING

if (DEFAULT_PLAYER_VOLUME := data.get("PYLAV__DEFAULT_PLAYER_VOLUME")) is None:
    DEFAULT_PLAYER_VOLUME = int(os.getenv("PYLAV__DEFAULT_PLAYER_VOLUME", "25"))
    data_new["PYLAV__DEFAULT_PLAYER_VOLUME"] = DEFAULT_PLAYER_VOLUME

data_new = _remove_keys(
    "PYLAV__CACHING_ENABLED",
    "PYLAV__PREFER_PARTIAL_TRACKS",
    "PREFER_PARTIAL_TRACKS",
    "PYLAV__LINKED_BOT_IDS",
    data=data_new,
)

if os.access(ENV_FILE, os.W_OK) and DeepDiff(data, data_new, ignore_order=True, max_passes=2, cache_size=1000):
    with ENV_FILE.open(mode="w") as file:
        LOGGER.info("Updating %s with the following content: %r", ENV_FILE, data_new)
        yaml.safe_dump(data_new, file, default_flow_style=False, sort_keys=False, encoding="utf-8")
