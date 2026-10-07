"""OPC UA client: find GVLMain on the CODESYS server and poll it.

CODESYS exposes the symbol configuration under its own namespace, and both the
namespace index and the path down to the application vary with the device name
and the CODESYS version. Rather than hard-coding a node id like

    ns=4;s=|var|CODESYS Control Win V3 x64.Application.GVLMain.Pen.Status.OverHeight

this module *browses* for a node called GVLMain and then walks down from it by
browse name. That survives a rename of the device or the application, which is
the thing most likely to differ between your machine and anyone else's.

Run `python -m cashmi.browse` to dump what the server actually publishes.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from asyncua import Client, Node, ua

from . import tags as T

log = logging.getLogger("cashmi.opc")

_INDEX_RE = re.compile(r"^(?P<name>[^\[\]]+)\[(?P<index>\d+)\]$")


class Resolver:
    """Turns a dotted GVLMain path into an OPC UA node."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.root: Optional[Node] = None
        self._cache: Dict[str, Node] = {}

    async def find_gvlmain(self, max_depth: int = 8) -> Node:
        """Breadth-first search from Objects for a node named GVLMain."""
        objects = self.client.nodes.objects
        frontier: List[Tuple[Node, int]] = [(objects, 0)]
        seen: set = set()

        while frontier:
            node, depth = frontier.pop(0)
            if depth > max_depth:
                continue
            try:
                children = await node.get_children()
            except ua.UaError:
                continue

            for child in children:
                node_id = child.nodeid.to_string()
                if node_id in seen:
                    continue
                seen.add(node_id)
                try:
                    name = (await child.read_browse_name()).Name
                except ua.UaError:
                    continue
                if name == "GVLMain":
                    log.info("found GVLMain at %s", node_id)
                    self.root = child
                    return child
                frontier.append((child, depth + 1))

        raise RuntimeError(
            "GVLMain not found on the OPC UA server.\n"
            "Check that the CODESYS application has a Symbol Configuration that\n"
            "includes GVLMain, that it has been downloaded, and that the PLC is\n"
            "in RUN. Use `python -m cashmi.browse` to see what is published."
        )

    async def _child_named(self, parent: Node, name: str) -> Optional[Node]:
        for child in await parent.get_children():
            try:
                if (await child.read_browse_name()).Name == name:
                    return child
            except ua.UaError:
                continue
        return None

    async def resolve(self, path: str) -> Node:
        """Resolve a dotted path below GVLMain, e.g. `Inlet[0].InletPump.State`."""
        if path in self._cache:
            return self._cache[path]
        if self.root is None:
            await self.find_gvlmain()
        assert self.root is not None

        # Fast path: CODESYS string node ids are the variable path itself
        # (`...Application.GVLMain.Inlet[0].InletPump.State`), so one read
        # confirms the node. Walking by browse name reads every child's name at
        # each level, thousands of requests for an array of structs; over a slow
        # link the PFC200 drops the connection before the walk is done.
        root_id = self.root.nodeid
        if root_id.NodeIdType == ua.NodeIdType.String:
            direct = self.client.get_node(
                ua.NodeId(f"{root_id.Identifier}.{path}", root_id.NamespaceIndex)
            )
            try:
                await direct.read_browse_name()
                self._cache[path] = direct
                return direct
            except ua.UaError:
                pass  # not a CODESYS-style id; browse instead

        node = self.root
        for segment in path.split("."):
            match = _INDEX_RE.match(segment)
            if match:
                base, index = match.group("name"), match.group("index")
                # CODESYS publishes array members either as one node whose browse
                # name carries the subscript, or as an array node with indexed
                # children. The PFC200 (750-8212) names those children
                # `Inlet[0]` under `Inlet`; other spellings are `[0]` and `0`.
                found = await self._child_named(node, f"{base}[{index}]")
                if found is None:
                    array_node = await self._child_named(node, base)
                    if array_node is not None:
                        for name in (f"{base}[{index}]", f"[{index}]", index):
                            found = await self._child_named(array_node, name)
                            if found is not None:
                                break
                node = found  # type: ignore[assignment]
            else:
                node = await self._child_named(node, segment)  # type: ignore[assignment]

            if node is None:
                raise RuntimeError(f"cannot resolve {path!r}: no child {segment!r}")

        self._cache[path] = node
        return node


class History:
    """Two ring buffers, one per time base, plus the latest value of every tag."""

    def __init__(self) -> None:
        self.fast: Deque[Dict[str, Any]] = deque(maxlen=T.FAST_POINTS)
        self.slow: Deque[Dict[str, Any]] = deque(maxlen=T.SLOW_POINTS)
        self.latest: Dict[str, Any] = {}
        self.connected = False
        self.error: Optional[str] = None
        self._last_slow = 0.0

    def record(self, values: Dict[str, Any], now: float) -> None:
        self.latest = values
        fast = {"t": now}
        fast.update({k: v for k, v in values.items() if k not in T.SLOW_KEYS})
        self.fast.append(fast)

        if now - self._last_slow >= T.SLOW_PERIOD_S:
            self._last_slow = now
            slow = {"t": now}
            slow.update({k: values.get(k) for k in T.SLOW_KEYS})
            self.slow.append(slow)


class Collector:
    """Polls the PLC and keeps `History` up to date. Reconnects on its own."""

    def __init__(self, url: str, history: History, period: float = T.FAST_PERIOD_S,
                 user: Optional[str] = None, password: Optional[str] = None) -> None:
        self.url = url
        self.history = history
        self.period = period
        self.user = user
        self.password = password
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                backoff = 1.0
            except Exception as exc:  # noqa: BLE001 - any failure means reconnect
                self.history.connected = False
                self.history.error = f"{type(exc).__name__}: {exc}"
                log.warning("disconnected (%s); retrying in %.0fs",
                            self.history.error, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 15.0)

    async def _session(self) -> None:
        client = Client(url=self.url)
        if self.user:
            client.set_user(self.user)
            if self.password:
                client.set_password(self.password)

        async with client:
            resolver = Resolver(client)
            await resolver.find_gvlmain()

            nodes: Dict[str, Node] = {}
            missing: List[str] = []
            for tag in T.ALL_TAGS:
                try:
                    nodes[tag.key] = await resolver.resolve(tag.path)
                except Exception as exc:  # noqa: BLE001
                    missing.append(f"{tag.path} ({exc})")

            if missing:
                log.warning("%d tag(s) not found and will read as null:\n  %s",
                            len(missing), "\n  ".join(missing))
            if not nodes:
                raise RuntimeError("no tags resolved; nothing to display")

            # Quality flags. A flag that does not resolve (an older PLC
            # project) is left out, and its value is shown as it reads.
            bad_nodes: Dict[str, Node] = {}
            for tag in T.ALL_TAGS:
                if tag.bad_path and tag.key in nodes:
                    try:
                        bad_nodes[tag.key] = await resolver.resolve(tag.bad_path)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("quality flag %s not found (%s); %s is shown unchecked",
                                    tag.bad_path, exc, tag.key)

            log.info("polling %d tag(s) and %d quality flag(s) every %.1fs",
                     len(nodes), len(bad_nodes), self.period)
            self.history.connected = True
            self.history.error = None

            keys = list(nodes)
            bad_keys = list(bad_nodes)
            node_list = [nodes[k] for k in keys] + [bad_nodes[k] for k in bad_keys]

            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    raw = await client.read_values(node_list)
                except Exception:
                    raise  # bubble up to reconnect

                bad = {k for k, flag in zip(bad_keys, raw[len(keys):]) if flag is True}
                values: Dict[str, Any] = {}
                for key, value in zip(keys, raw[:len(keys)]):
                    if key in bad:
                        values[key] = None  # PLC marks it untrustworthy
                        continue
                    tag = T.BY_KEY[key]
                    if isinstance(value, bool):
                        values[key] = value
                    elif isinstance(value, (int, float)):
                        values[key] = tag.convert(float(value)) if tag.convert else float(value)
                    else:
                        values[key] = value
                for key in T.BY_KEY:
                    values.setdefault(key, None)

                self.history.record(values, time.time())

                elapsed = time.monotonic() - started
                try:
                    await asyncio.wait_for(self._stop.wait(),
                                           timeout=max(0.0, self.period - elapsed))
                except asyncio.TimeoutError:
                    pass
