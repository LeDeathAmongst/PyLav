from __future__ import annotations

import os
import pathlib

import yaml

from pylav.logging import getLogger

if full_path := os.getenv("PYLAV__YAML_CONFIG"):
    ENV_FILE = pathlib.Path(full_path)
else:
    ENV_FILE = pathlib.Path.home() / "pylav.yaml"

LOGGER = getLogger("PyLav.Environment")


def build_from_envvars() -> None:
    from pylav.constants.config.env_var import (
        DATA_FOLDER,
        DEFAULT_SEARCH_SOURCE,
        EXTERNAL_UNMANAGED_HOST,
        EXTERNAL_UNMANAGED_NAME,
        EXTERNAL_UNMANAGED_PASSWORD,
        EXTERNAL_UNMANAGED_PORT,
        EXTERNAL_UNMANAGED_SSL,
        FALLBACK_POSTGREST_HOST,
        JAVA_EXECUTABLE,
        LOCAL_TRACKS_FOLDER,
        MANAGED_NODE_APPLE_MUSIC_API_KEY,
        MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE,
        MANAGED_NODE_DEEZER_KEY,
        MANAGED_NODE_SPOTIFY_CLIENT_ID,
        MANAGED_NODE_SPOTIFY_CLIENT_SECRET,
        MANAGED_NODE_SPOTIFY_COUNTRY_CODE,
        MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN,
        POSTGRES_DATABASE,
        POSTGRES_PASSWORD,
        POSTGRES_PORT,
        POSTGRES_SOCKET,
        POSTGRES_USER,
        READ_CACHING_ENABLED,
        REDIS_FULL_ADDRESS_RESPONSE_CACHE,
        TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS,
        TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS,
        TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS,
    )

    # noinspection SpellCheckingInspection
    data = {
        "PYLAV__POSTGRES_PORT": POSTGRES_PORT,
        "PYLAV__POSTGRES_PASSWORD": POSTGRES_PASSWORD,
        "PYLAV__POSTGRES_USER": POSTGRES_USER,
        "PYLAV__POSTGRES_DB": POSTGRES_DATABASE,
        "PYLAV__POSTGRES_SOCKET": POSTGRES_SOCKET,
        "PYLAV__POSTGRES_HOST": FALLBACK_POSTGREST_HOST,
        "PYLAV__REDIS_FULLADDRESS_RESPONSE_CACHE": REDIS_FULL_ADDRESS_RESPONSE_CACHE,
        "PYLAV__JAVA_EXECUTABLE": JAVA_EXECUTABLE,
        "PYLAV__EXTERNAL_UNMANAGED_HOST": EXTERNAL_UNMANAGED_HOST,
        "PYLAV__EXTERNAL_UNMANAGED_PORT": EXTERNAL_UNMANAGED_PORT,
        "PYLAV__EXTERNAL_UNMANAGED_PASSWORD": EXTERNAL_UNMANAGED_PASSWORD,
        "PYLAV__EXTERNAL_UNMANAGED_SSL": EXTERNAL_UNMANAGED_SSL,
        "PYLAV__EXTERNAL_UNMANAGED_NAME": EXTERNAL_UNMANAGED_NAME,
        "PYLAV__TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS": TASK_TIMER_UPDATE_BUNDLED_PLAYLISTS_DAYS,
        "PYLAV__TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS": TASK_TIMER_UPDATE_BUNDLED_EXTERNAL_PLAYLISTS_DAYS,
        "PYLAV__TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS": TASK_TIMER_UPDATE_EXTERNAL_PLAYLISTS_DAYS,
        "PYLAV__READ_CACHING_ENABLED": READ_CACHING_ENABLED,
        "PYLAV__DEFAULT_SEARCH_SOURCE": DEFAULT_SEARCH_SOURCE,
        "PYLAV__MANAGED_NODE_SPOTIFY_CLIENT_ID": MANAGED_NODE_SPOTIFY_CLIENT_ID,
        "PYLAV__MANAGED_NODE_SPOTIFY_CLIENT_SECRET": MANAGED_NODE_SPOTIFY_CLIENT_SECRET,
        "PYLAV__MANAGED_NODE_SPOTIFY_COUNTRY_CODE": MANAGED_NODE_SPOTIFY_COUNTRY_CODE,
        "PYLAV__MANAGED_NODE_APPLE_MUSIC_API_KEY": MANAGED_NODE_APPLE_MUSIC_API_KEY,
        "PYLAV__MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE": MANAGED_NODE_APPLE_MUSIC_COUNTRY_CODE,
        "PYLAV__MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN": MANAGED_NODE_YANDEX_MUSIC_ACCESS_TOKEN,
        "PYLAV__MANAGED_NODE_DEEZER_KEY": MANAGED_NODE_DEEZER_KEY,
        "PYLAV__LOCAL_TRACKS_FOLDER": LOCAL_TRACKS_FOLDER,
        "PYLAV__DATA_FOLDER": DATA_FOLDER,
    }
    with ENV_FILE.open(mode="w") as file:
        LOGGER.debug("Creating %s with the following content: %r", ENV_FILE, data)
        yaml.safe_dump(data, file, default_flow_style=False, sort_keys=False, encoding="utf-8")
