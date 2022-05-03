from __future__ import annotations

import asyncio
import asyncio.subprocess  # disables for # https://github.com/PyCQA/pylint/issues/1469
import contextlib
import itertools
import pathlib
import platform
import re
import shlex
import shutil
import tempfile
from typing import TYPE_CHECKING, ClassVar, Final, Pattern

import aiohttp
import aiopath
import dateutil.parser
import psutil
import rich.progress
import ujson
import yaml
from red_commons.logging import getLogger

from pylav._config import CONFIG_DIR
from pylav.exceptions import (
    EarlyExitError,
    IncorrectProcessFound,
    InvalidArchitectureError,
    LavalinkDownloadFailed,
    ManagedLavalinkNodeError,
    ManagedLavalinkStartFailure,
    ManagedLinkStartAbortedUseExternal,
    NodeUnhealthy,
    NoProcessFound,
    PortAlreadyInUseError,
    TooManyProcessFound,
    UnexpectedJavaResponseError,
    UnsupportedJavaError,
    WebsocketNotConnectedError,
)
from pylav.node import Node
from pylav.sql.models import NodeModel
from pylav.utils import AsyncIter, ExponentialBackoffWithReset

if TYPE_CHECKING:
    from pylav.client import Client

LOGGER = getLogger("red.PyLink.ManagedNode")

LAVALINK_DOWNLOAD_DIR = CONFIG_DIR / "lavalink"
LAVALINK_DOWNLOAD_DIR = pathlib.Path(LAVALINK_DOWNLOAD_DIR)  # type: ignore
LAVALINK_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
LAVALINK_DOWNLOAD_DIR: aiopath.AsyncPath = aiopath.AsyncPath(LAVALINK_DOWNLOAD_DIR)
LAVALINK_JAR_FILE: Final[aiopath.AsyncPath] = LAVALINK_DOWNLOAD_DIR / "Lavalink.jar"
LAVALINK_APP_YML: Final[aiopath.AsyncPath] = LAVALINK_DOWNLOAD_DIR / "application.yml"

_RE_READY_LINE: Final[Pattern] = re.compile(rb"Started Launcher in \S+ seconds")
_FAILED_TO_START: Final[Pattern] = re.compile(rb"Web server failed to start\. (.*)")
_RE_BUILD_LINE: Final[Pattern] = re.compile(rb"Build:\s+(?P<build>\d+)")

# Version regexes
#
# We expect the output to look something like:
#     $ java -version
#     ...
#     ... version "VERSION STRING HERE" ...
#     ...
#
# There are two version formats that we might get here:
#
# - Version scheme pre JEP 223 - used by Java 8 and older
#
# examples:
# 1.8.0
# 1.8.0_275
# 1.8.0_272-b10
# 1.8.0_202-internal-201903130451-b08
# 1.8.0_272-ea-202010231715-b10
# 1.8.0_272-ea-b10
#
# Implementation based on J2SE SDK/JRE Version String Naming Convention document:
# https://www.oracle.com/java/technologies/javase/versioning-naming.html
_RE_JAVA_VERSION_LINE_PRE223: Final[Pattern] = re.compile(
    r'version "1\.(?P<major>[0-8])\.(?P<minor>0)(?:_\d+)?(?:-.*)?"'
)
# - Version scheme introduced by JEP 223 - used by Java 9 and newer
#
# examples:
# 11
# 11.0.9
# 11.0.9.1
# 11.0.9-ea
# 11.0.9-202011050024
#
# Implementation based on JEP 223 document:
# https://openjdk.java.net/jeps/223
_RE_JAVA_VERSION_LINE_223: Final[Pattern] = re.compile(
    r'version "(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.\d+)*(-[a-zA-Z\d]+)?"'
)

LAVALINK_BRANCH_LINE: Final[Pattern] = re.compile(rb"Branch\s+(?P<branch>[\w\-\d_.]+)")
LAVALINK_JAVA_LINE: Final[Pattern] = re.compile(rb"JVM:\s+(?P<jvm>\d+[.\d+]*)")
LAVALINK_LAVAPLAYER_LINE: Final[Pattern] = re.compile(rb"Lavaplayer\s+(?P<lavaplayer>\d+[.\d+]*)")
LAVALINK_BUILD_TIME_LINE: Final[Pattern] = re.compile(rb"Build time:\s+(?P<build_time>\d+[.\d+]*)")
JAR_SERVER = "https://ci.fredboat.com"
JAR_SERVER_BUILD_INFO = (
    "/guestAuth/app/rest/builds?locator=branch:refs/heads/dev,buildType:Lavalink_Build,status:SUCCESS,count:1"
)
BUILD_META_KEYS = ("number", "branchName", "finishDate", "href")
# This is a fallback URL for when the above doesn't return a valid input
#   This will download from the Master branch which is behind dev
LAVALINK_JAR_ENDPOINT: Final[
    str
] = "https://ci.fredboat.com/guestAuth/repository/download/Lavalink_Build/.lastSuccessful/Lavalink.jar"


def convert_function(key: str) -> str:
    return key.replace("_", "-")


def change_dict_naming_convention(data: dict) -> dict:
    new = {}
    for k, v in data.items():
        new_v = v
        if isinstance(v, dict):
            new_v = change_dict_naming_convention(v)
        elif isinstance(v, list):
            new_v = list()
            for x in v:
                if isinstance(x, dict):
                    new_v.append(change_dict_naming_convention(x))
                else:
                    new_v.append(x)
        new[convert_function(k)] = new_v
    return new


def get_max_allocation_size(executable: str) -> tuple[int, bool]:
    if platform.architecture(executable)[0] == "64bit":
        max_heap_allowed = psutil.virtual_memory().total
        thinks_is_64_bit = True
    else:
        max_heap_allowed = 4 * 1024**3
        thinks_is_64_bit = False
    return max_heap_allowed, thinks_is_64_bit


class LocalNodeManager:

    _java_available: ClassVar[bool | None] = None
    _java_version: ClassVar[tuple[int, int] | None] = None
    _up_to_date: ClassVar[bool | None] = None
    _blacklisted_archs: list[str] = []

    _lavaplayer: ClassVar[str | None] = None
    _lavalink_build: ClassVar[int | None] = None
    _jvm: ClassVar[str | None] = None
    _lavalink_branch: ClassVar[str | None] = None
    _buildtime: ClassVar[str | None] = None
    _java_exc: ClassVar[str] = "java"

    def __init__(self, client: Client, timeout: int | None = None, auto_update: bool = True) -> None:
        self._auto_update = auto_update
        self.ready: asyncio.Event = asyncio.Event()
        self._ci_info: dict = {"number": 0, "branchName": "", "finishDate": "", "href": "", "jar_url": ""}
        self._client = client
        self._proc: asyncio.subprocess.Process | None = None  # pylint:disable=no-member
        self._node_pid: int | None = None
        self._shutdown: bool = False
        self.start_monitor_task = None
        self.timeout = timeout
        self._args = []
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), json_serialize=ujson.dumps)
        self._node_id: int = self._client.bot.user.id
        self._node: Node | None = None
        self._current_config = {}
        self._full_data: NodeModel = None  # type: ignore
        self.abort_for_unmanaged: asyncio.Event = asyncio.Event()
        self._args = []
        self._wait_for = asyncio.Event()
        self._java_path = None

    @property
    def node(self) -> Node | None:
        return self._node

    @property
    def path(self) -> str | None:
        return self._java_exc

    @property
    def jvm(self) -> str | None:
        return self._jvm

    @property
    def lavaplayer(self) -> str | None:
        return self._lavaplayer

    @property
    def ll_build(self) -> int | None:
        return self._lavalink_build

    @property
    def ll_branch(self) -> str | None:
        return self._lavalink_branch

    @property
    def build_time(self) -> str | None:
        return self._buildtime

    async def get_ci_latest_info(self) -> dict:
        async with self._client.cached_session.get(
            f"{JAR_SERVER}{JAR_SERVER_BUILD_INFO}", headers={"Accept": "application/json"}
        ) as response:
            if response.status != 200:
                return {"number": -1}
            data = await response.json(loads=ujson.loads)
            data = data["build"][0]
            returning_data = {}
            for k in BUILD_META_KEYS:
                if "finishDate" == k:
                    returning_data[k] = dateutil.parser.parse(data["finishOnAgentDate"])
                elif "number" == k:
                    returning_data[k] = int(data[k])
                else:
                    returning_data[k] = data[k]
        async with self._client.cached_session.get(
            f"{JAR_SERVER}{returning_data['href']}", headers={"Accept": "application/json"}
        ) as response:
            data = await response.json(loads=ujson.loads)
            returning_data["href"] = data["artifacts"]["href"]

        async with self._client.cached_session.get(
            f"{JAR_SERVER}{returning_data['href']}", headers={"Accept": "application/json"}
        ) as response:
            data = await response.json(loads=ujson.loads)
            jar_meta = filter(lambda x: x["name"] == "Lavalink.jar", data["file"])
            jar_url = next(jar_meta).get("content", {}).get("href")
            returning_data["jar_url"] = jar_url
        return returning_data

    async def _start(self, java_path: str) -> None:
        arch_name = platform.machine()
        self._java_exc = java_path
        if arch_name in self._blacklisted_archs:
            raise InvalidArchitectureError(
                "You are attempting to run the managed Lavalink node on an unsupported machine architecture."
            )
        await self.process_settings()
        possible_lavalink_processes = await self.get_lavalink_process(lazy_match=True)
        if possible_lavalink_processes:
            LOGGER.info(
                "Found %s processes that match potential unnamaged Lavalink nodes.",
                len(possible_lavalink_processes),
            )
            valid_working_dirs = [
                cwd for d in possible_lavalink_processes if d.get("name") == "java" and (cwd := d.get("cwd"))
            ]
            LOGGER.debug("Found %s java processed with a cwd set.", len(valid_working_dirs))
            for cwd in valid_working_dirs:
                config = aiopath.AsyncPath(cwd) / "application.yml"
                if await config.exists() and await config.is_file():
                    LOGGER.debug(
                        "The following config file exists for an unmanaged Lavalink node %s",
                        config,
                    )
                    try:
                        async with config.open(mode="r") as config_data:
                            data = yaml.safe_load(await config_data.read())
                            data["server"]["address"]  # noqa
                            data["server"]["port"]  # noqa
                            data["lavalink"]["server"]["password"]  # noqa
                            self._node_pid = 0
                            self._current_config = data
                            raise ManagedLinkStartAbortedUseExternal
                    except ManagedLinkStartAbortedUseExternal:
                        raise
                    except Exception:
                        LOGGER.exception("Failed to read contents of %s", config)
                        continue

        await self.maybe_download_jar()
        args, msg = await self._get_jar_args()
        if msg is not None:
            LOGGER.warning(msg)
        command_string = shlex.join(args)
        LOGGER.info("Managed Lavalink node startup command: %s", command_string)
        if "-Xmx" not in command_string and msg is None:
            LOGGER.warning("Managed Lavalink node maximum allowed RAM not set or higher than available RAM")
            # FIXME: Add instruction for user to set max RAM
        try:
            self._proc = await asyncio.subprocess.create_subprocess_exec(  # pylint:disable=no-member
                *args,
                cwd=str(LAVALINK_DOWNLOAD_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._node_pid = self._proc.pid
            LOGGER.info("Managed Lavalink node started. PID: %s", self._node_pid)
            try:
                await asyncio.wait_for(self._wait_for_launcher(), timeout=self.timeout)
            except asyncio.TimeoutError:
                LOGGER.warning("Timeout occurred whilst waiting for managed Lavalink node to be ready")
                raise
        except asyncio.TimeoutError:
            await self._partial_shutdown()
        except Exception:
            await self._partial_shutdown()
            raise

    async def process_settings(self):
        self._full_data = await self._client.node_db_manager.get_bundled_node_config()
        data = change_dict_naming_convention(self._full_data.yaml)
        # The reason this is here is to completely remove these keys from the application.yml
        # if they are set to empty values
        if not all(
            (
                data["lavalink"]["server"]["youtubeConfig"]["PAPISID"],
                data["lavalink"]["server"]["youtubeConfig"]["PSID"],
            )
        ):
            del data["lavalink"]["server"]["youtubeConfig"]
        if not data["lavalink"]["server"]["ratelimit"]["ipBlocks"]:
            del data["lavalink"]["server"]["ratelimit"]
        if data["sentry"]["dsn"]:
            data["sentry"]["tags"]["ID"] = self._client.bot.user.id
            data["sentry"]["tags"]["pylav_version"] = self._client.lib_version
        if not data["lavalink"]["server"]["httpConfig"] and not data["lavalink"]["server"]["httpConfig"]["proxyHost"]:
            del data["lavalink"]["server"]["httpConfig"]
        self._current_config = data
        async with LAVALINK_APP_YML.open("w") as f:
            await f.write(yaml.safe_dump(data))

    async def _get_jar_args(self) -> tuple[list[str], str | None]:
        (java_available, java_version) = await self._has_java()

        if not java_available:
            if self._java_version is None:
                extras = ""
            else:
                extras = f" however you have version {self._java_version} (executable: {self._java_exc})"
            raise UnsupportedJavaError()  # FIXME: Add API endpoint to change this
        java_xms, java_xmx = "64M", self._full_data.extras.get("max_ram", "2048M") if self._full_data else "2048M"
        match = re.match(r"^(\d+)([MG])$", java_xmx, flags=re.IGNORECASE)
        command_args = [
            self._java_exc,
            "-Djdk.tls.client.protocols=TLSv1.2",
            f"-Xms{java_xms}",
        ]
        meta = 0, None
        invalid = None
        if match and (
            (int(match.group(1)) * 1024 ** (2 if match.group(2).lower() == "m" else 3))
            <= (meta := get_max_allocation_size(self._java_exc))[0]
        ):
            command_args.append(f"-Xmx{java_xmx}")
        elif meta[0] is not None:
            # FIXME: Add API endpoint to change this
            invalid = "Managed Lavalink node RAM allocation ignored due to system limitations, please fix this."

        command_args.extend(["-jar", str(LAVALINK_JAR_FILE)])
        self._args = command_args
        return command_args, invalid

    async def _has_java(self) -> tuple[bool, tuple[int, int] | None]:
        if self._java_available:
            # Return cached value if we've checked this before
            return self._java_available, self._java_version
        java_exec = shutil.which(self._java_exc)
        java_available = java_exec is not None
        if not java_available:
            self._java_available = False
            self._java_version = None
        else:
            self._java_version = await self._get_java_version()
            self._java_available = (11, 0) >= self._java_version  # https://github.com/freyacodes/Lavalink#requirements
            self._java_exc = java_exec
        return self._java_available, self._java_version

    async def _get_java_version(self) -> tuple[int, int]:
        """This assumes we've already checked that java exists."""
        _proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(  # pylint:disable=no-member
            self._java_exc,
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # java -version outputs to stderr
        _, err = await _proc.communicate()

        version_info: str = err.decode("utf-8")
        lines = version_info.splitlines()
        for line in lines:
            match = _RE_JAVA_VERSION_LINE_PRE223.search(line)
            if match is None:
                match = _RE_JAVA_VERSION_LINE_223.search(line)
            if match is None:
                continue
            major = int(match["major"])
            minor = 0
            if minor_str := match["minor"]:
                minor = int(minor_str)

            return major, minor

        raise UnexpectedJavaResponseError(f"The output of `{self._java_exc} -version` was unexpected\n{version_info}.")

    async def _wait_for_launcher(self) -> None:
        LOGGER.info("Waiting for Managed Lavalink node to be ready")
        for i in itertools.cycle(range(50)):
            line = await self._proc.stdout.readline()
            if _RE_READY_LINE.search(line):
                self.ready.set()
                LOGGER.info("Managed Lavalink node is ready to receive requests.")
                break
            if _FAILED_TO_START.search(line):
                if f"Port {self._current_config['server']['port']} was already in use".encode() in line:
                    raise PortAlreadyInUseError(
                        f"Port {self._current_config['server']['port']} already in use. "
                        "Managed Lavalink startup aborted."
                    )
                raise ManagedLavalinkStartFailure(f"Lavalink failed to start: {line.decode().strip()}")
            if self._proc.returncode is not None:
                # Avoid Console spam only print once every 2 seconds
                raise EarlyExitError("Managed Lavalink node server exited early.")
            if i == 49:
                # Sleep after 50 lines to prevent busylooping
                await asyncio.sleep(0.1)

    async def shutdown(self) -> None:
        if self.start_monitor_task is not None:
            self.start_monitor_task.cancel()
        await self._partial_shutdown()
        await self._session.close()
        if self.node:
            await self._client.node_manager.remove_node(self.node)

    async def _partial_shutdown(self) -> None:
        self.ready.clear()
        self._wait_for.clear()
        self.abort_for_unmanaged.clear()
        # In certain situations to await self._proc.wait() is invalid so waiting on it waits forever.
        if self._shutdown is True:
            # For convenience, calling this method more than once or calling it before starting it
            # does nothing.
            return
        if self._node_pid:
            with contextlib.suppress(psutil.Error):
                p = psutil.Process(self._node_pid)
                p.terminate()
                p.kill()
        if self._proc is not None:
            if self._proc.returncode is None:
                self._proc.terminate()
                self._proc.kill()
                await self._proc.wait()
        self._proc = None
        self._shutdown = True
        self._node_pid = None
        if self._node is not None:
            await self._client.remove_node(self._node_id)
            self._node = None

    async def _download_jar(self) -> None:
        if not self._auto_update:
            return
        LOGGER.info("Downloading Lavalink.jar...")
        if self._ci_info["jar_url"]:
            jar_url = JAR_SERVER + self._ci_info["jar_url"]
        else:
            jar_url = LAVALINK_JAR_ENDPOINT
        async with self._session.get(jar_url, timeout=600) as response:
            if 400 <= response.status < 600:
                raise LavalinkDownloadFailed(response=response, should_retry=True)
            fd, path = tempfile.mkstemp()
            file = open(fd, "wb")
            nbytes = 0
            with rich.progress.Progress(
                rich.progress.SpinnerColumn(),
                rich.progress.TextColumn("[progress.description]{task.description}"),
                rich.progress.BarColumn(),
                rich.progress.TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                rich.progress.TimeRemainingColumn(),
                rich.progress.TimeElapsedColumn(),
            ) as progress:
                progress_task_id = progress.add_task("[red]Downloading Lavalink.jar", total=response.content_length)
                try:
                    chunk = await response.content.read(1024)
                    while chunk:
                        chunk_size = file.write(chunk)
                        nbytes += chunk_size
                        progress.update(progress_task_id, advance=chunk_size)
                        chunk = await response.content.read(1024)
                    file.flush()
                finally:
                    file.close()

            shutil.move(path, str(LAVALINK_JAR_FILE), copy_function=shutil.copyfile)

        LOGGER.info("Successfully downloaded Lavalink.jar (%s bytes written)", format(nbytes, ","))
        await self._is_up_to_date()

    async def _is_up_to_date(self):
        if self._up_to_date is True:
            # Return cached value if we've checked this before
            return True
        args, _ = await self._get_jar_args()
        args.append("--version")
        _proc = await asyncio.subprocess.create_subprocess_exec(  # pylint:disable=no-member
            *args,
            cwd=str(LAVALINK_DOWNLOAD_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout = (await _proc.communicate())[0]
        if (build := _RE_BUILD_LINE.search(stdout)) is None:
            # Output is unexpected, suspect corrupted jarfile
            return False
        if (branch := LAVALINK_BRANCH_LINE.search(stdout)) is None:
            # Output is unexpected, suspect corrupted jarfile
            return False
        if (java := LAVALINK_JAVA_LINE.search(stdout)) is None:
            # Output is unexpected, suspect corrupted jarfile
            return False
        if (lavaplayer := LAVALINK_LAVAPLAYER_LINE.search(stdout)) is None:
            # Output is unexpected, suspect corrupted jarfile
            return False
        if (buildtime := LAVALINK_BUILD_TIME_LINE.search(stdout)) is None:
            # Output is unexpected, suspect corrupted jarfile
            return False

        build = int(build["build"])
        date = buildtime["build_time"].decode()
        date = date.replace(".", "/")
        self._lavalink_build = build
        self._lavalink_branch = branch["branch"].decode()
        self._jvm = java["jvm"].decode()
        self._lavaplayer = lavaplayer["lavaplayer"].decode()
        self._buildtime = date
        if self._auto_update:
            self._up_to_date = build == self._ci_info.get("number")
        else:
            self._ci_info["number"] = build
            self._up_to_date = True
        return self._up_to_date

    async def maybe_download_jar(self):
        self._ci_info = await self.get_ci_latest_info()
        if not (await LAVALINK_JAR_FILE.exists() and await self._is_up_to_date()):
            await self._download_jar()

    async def wait_until_ready(self, timeout: float | None = None):
        tasks = [asyncio.create_task(c) for c in [self.ready.wait(), self.abort_for_unmanaged.wait()]]
        done, pending = await asyncio.wait(tasks, timeout=timeout or self.timeout, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if done:
            done.pop().result()
        if self.abort_for_unmanaged.is_set():
            raise asyncio.TimeoutError
        if not self.ready.is_set():
            raise asyncio.TimeoutError

    async def wait_until_connected(self, timeout: float | None = None):
        tasks = [asyncio.create_task(c) for c in [self._wait_for.wait(), self.wait_until_ready()]]
        done, pending = await asyncio.wait(tasks, timeout=timeout or self.timeout, return_when=asyncio.ALL_COMPLETED)
        for task in pending:
            task.cancel()

    async def start_monitor(self, java_path: str):
        retry_count = 0
        backoff = ExponentialBackoffWithReset(base=3)
        while True:
            try:
                self._shutdown = False
                if self._node_pid is None or not psutil.pid_exists(self._node_pid):
                    self.ready.clear()
                    await self._start(java_path=java_path)
                while True:
                    await self.wait_until_ready(timeout=self.timeout)
                    if not psutil.pid_exists(self._node_pid):
                        raise NoProcessFound
                    if self._node is None or not self._node.websocket.connected and not self._node.websocket.connecting:
                        await self.connect_node(reconnect=retry_count != 0, wait_for=3)
                    try:
                        node = self._client.node_manager.get_node_by_id(self._node_id)
                        if node is not None:
                            await node.wait_until_ready(timeout=30)
                        if node.websocket.connected:
                            try:
                                # Hoping this throws an exception which will then trigger a restart
                                await node.websocket.ping()
                                backoff.reset()
                                await asyncio.sleep(1)
                            except WebsocketNotConnectedError:
                                await asyncio.sleep(5)
                        elif node.websocket.connecting:
                            await node.websocket.wait_until_ready(timeout=30)
                        else:
                            raise AttributeError
                    except AttributeError as e:
                        try:
                            LOGGER.debug(
                                "Managed node monitor detected RLL is not connected to any nodes -%s", exc_info=e
                            )
                            while True:
                                node = self._client.node_manager.get_node_by_id(self._node_id)
                                if node is not None:
                                    await node.wait_until_ready(timeout=30)
                                if node and node.websocket.connected:
                                    break
                                await asyncio.sleep(1)
                        except asyncio.TimeoutError:
                            raise
                    except Exception as exc:
                        LOGGER.debug(exc, exc_info=exc)
                        raise NodeUnhealthy(str(exc))
            except (TooManyProcessFound, IncorrectProcessFound, NoProcessFound):
                await self._partial_shutdown()
            except asyncio.TimeoutError:
                delay = backoff.delay()
                await self._partial_shutdown()
                LOGGER.warning(
                    "Lavalink Managed node health check timeout, restarting in %s seconds",
                    delay,
                )
                await asyncio.sleep(delay)
            except ManagedLavalinkStartFailure:
                LOGGER.warning("Lavalink Managed node failed to start, restarting")
                await self._partial_shutdown()
                for process in await self.get_lavalink_process(
                    "-Djdk.tls.client.protocols=TLSv1.2", "-Xms64M", "-jar", cwd=str(LAVALINK_DOWNLOAD_DIR)
                ):
                    with contextlib.suppress(psutil.Error):
                        pid = process["pid"]
                        p = psutil.Process(pid)
                        p.terminate()
                        p.kill()
            except NodeUnhealthy:
                delay = backoff.delay()
                await self._partial_shutdown()
                LOGGER.warning(
                    "Lavalink Managed node health check failed, restarting in %s seconds",
                    delay,
                )
                await asyncio.sleep(delay)
            except LavalinkDownloadFailed as exc:
                delay = backoff.delay()
                if exc.should_retry:
                    LOGGER.warning(
                        "Lavalink Managed node download failed retrying in %s seconds\n%s",
                        delay,
                        exc.response,
                    )
                    retry_count += 1
                    await self._partial_shutdown()
                    await asyncio.sleep(delay)
                else:
                    LOGGER.critical(
                        "Fatal exception whilst starting managed Lavalink node, aborting...\n%s",
                        exc.response,
                    )
                    # lavalink_connection_aborted
                    return await self.shutdown()
            except InvalidArchitectureError:
                LOGGER.critical("Invalid machine architecture, cannot run a managed Lavalink node.")
                # lavalink_connection_aborted
                return await self.shutdown()
            except (UnsupportedJavaError, UnexpectedJavaResponseError) as exc:
                LOGGER.critical(exc)
                # lavalink_connection_aborted
                return await self.shutdown()
            except ManagedLinkStartAbortedUseExternal:
                LOGGER.warning("Lavalink Managed node start aborted, using the detected external Lavalink node.")
                await self.connect_node(reconnect=False, wait_for=0, external_fallback=True)
                return
            except ManagedLavalinkNodeError as exc:
                delay = backoff.delay()
                LOGGER.critical(
                    exc,
                )
                await self._partial_shutdown()
                LOGGER.warning(
                    "Lavalink Managed node startup failed retrying in %s seconds",
                    delay,
                )
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                LOGGER.warning("Lavalink Managed monitor task cancelled")
                return
            except Exception as exc:
                delay = backoff.delay()
                LOGGER.warning(
                    "Lavalink Managed node startup failed retrying in %s seconds",
                    delay,
                )
                LOGGER.info(exc, exc_info=exc)
                await self._partial_shutdown()
                await asyncio.sleep(delay)

    async def start(self, java_path: str):
        self._java_path = java_path
        if self.start_monitor_task is not None:
            await self.shutdown()
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), json_serialize=ujson.dumps)
        self._wait_for.clear()
        self.start_monitor_task = asyncio.create_task(self.start_monitor(java_path))
        self.start_monitor_task.set_name("LavalinkManagedNode.health_monitor")

    async def connect_node(self, reconnect: bool, wait_for: float = 0.0, external_fallback: bool = False):
        await asyncio.sleep(wait_for)
        self._wait_for.clear()
        if not self.ready.is_set():
            raise ManagedLavalinkStartFailure()
        if reconnect is True:
            node = self._client.node_manager.get_node_by_id(self._node_id)
            if node is not None:
                self._node = node
                if node.websocket.connecting:
                    await node.wait_until_ready(timeout=30)
                elif node.websocket.connected:
                    LOGGER.info("Managed Lavalink node is connected")
                else:
                    LOGGER.info("Managed Lavalink node is not connected, reconnecting...")
                    await node.websocket.close()
                    await node.websocket._websocket_closed(reason="Managed Node restart")
                    await node.wait_until_ready(timeout=30)
                self._wait_for.set()
                return
        if (node := self._client.node_manager.get_node_by_id(self._node_id)) is None:
            node = self._node = await self._client.add_node(
                host=self._current_config["server"]["address"],
                port=self._current_config["server"]["port"],
                password=self._current_config["lavalink"]["server"]["password"],
                resume_key=f"ManagedNode-{self._node_pid}-{self._node_id}",
                resume_timeout=self._full_data.resume_timeout,
                name=f"{self._full_data.name}: {self._node_pid}"
                if not external_fallback
                else f"PyLavPortConflictRecovery: {self._node_pid}",
                yaml=self._full_data.yaml,
                extras=self._full_data.extras,
                managed=True,
                ssl=False,
                search_only=False,
                unique_identifier=self._full_data.id,
                skip_db=True,
            )
        else:
            self._node = node
        if node.websocket.connecting:
            await node.wait_until_ready()
        elif node.websocket.connected:
            LOGGER.info("Managed Lavalink node is connected")
        else:
            LOGGER.info("Managed Lavalink node is not connected, reconnecting...")
            await node.websocket.close()
            await node.websocket._websocket_closed(reason="Managed Node restart")
            await node.wait_until_ready(timeout=30)
        self._wait_for.set()

    @staticmethod
    async def get_lavalink_process(*matches: str, cwd: str | None = None, lazy_match: bool = False):
        process_list = []
        filter_ = [cwd] if cwd else []
        async for proc in AsyncIter(psutil.process_iter()):
            try:
                if cwd and not (await asyncio.to_thread(proc.cwd) in filter_):
                    continue
                cmdline = await asyncio.to_thread(proc.cmdline)
                if (matches and all(a in cmdline for a in matches)) or (
                    lazy_match and any("lavalink" in arg.lower() for arg in cmdline)
                ):
                    proc_as_dict = await asyncio.to_thread(
                        proc.as_dict, attrs=["pid", "name", "create_time", "status", "cmdline", "cwd"]
                    )
                    process_list.append(proc_as_dict)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return process_list

    async def restart(self):
        LOGGER.info("Restarting managed Lavalink node.")
        await self.start(java_path=self._java_path)
