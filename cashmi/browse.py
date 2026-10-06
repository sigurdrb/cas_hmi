"""Dump what the CODESYS OPC UA server publishes, and check every HMI tag.

Run this first, or whenever a tag reads as "—" on the page:

    python -m cashmi.browse --url opc.tcp://localhost:4840

It prints the tree under GVLMain and then resolves each tag the HMI wants,
so a rename in the PLC project shows up immediately as a NOT FOUND line.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from asyncua import Client, Node, ua

from . import tags as T
from .opc import Resolver


async def dump(node: Node, depth: int, max_depth: int, prefix: str = "") -> None:
    if depth > max_depth:
        return
    try:
        children = await node.get_children()
    except ua.UaError:
        return
    for child in children:
        try:
            name = (await child.read_browse_name()).Name
            cls = await child.read_node_class()
        except ua.UaError:
            continue
        mark = "" if cls == ua.NodeClass.Variable else "/"
        value = ""
        if cls == ua.NodeClass.Variable:
            try:
                value = "  = " + repr(await child.read_value())[:60]
            except ua.UaError:
                value = "  = <unreadable>"
        print(f"{prefix}{name}{mark}{value}")
        if cls != ua.NodeClass.Variable:
            await dump(child, depth + 1, max_depth, prefix + "  ")


async def amain(args) -> int:
    client = Client(url=args.url)
    if args.user:
        client.set_user(args.user)
        if args.password:
            client.set_password(args.password)

    async with client:
        resolver = Resolver(client)
        root = await resolver.find_gvlmain()
        print(f"GVLMain node id: {root.nodeid.to_string()}\n")

        if args.tree:
            print(f"--- tree under GVLMain (depth {args.depth}) ---")
            await dump(root, 1, args.depth)
            print()

        print("--- HMI tags ---")
        missing = 0
        for tag in T.ALL_TAGS:
            try:
                node = await resolver.resolve(tag.path)
                value = await node.read_value()
                print(f"  OK        {tag.path:<52} = {value!r}")
            except Exception as exc:  # noqa: BLE001
                missing += 1
                print(f"  NOT FOUND {tag.path:<52}   {exc}")

        flags = [t for t in T.ALL_TAGS if t.bad_path]
        if flags:
            print("\n--- quality flags (a missing flag only means the value is shown unchecked) ---")
            for tag in flags:
                try:
                    node = await resolver.resolve(tag.bad_path)
                    value = await node.read_value()
                    print(f"  OK        {tag.bad_path:<64} = {value!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  NOT FOUND {tag.bad_path:<64}   {exc}")

        print(f"\n{len(T.ALL_TAGS) - missing}/{len(T.ALL_TAGS)} tags resolved.")
        if missing:
            print("Edit cashmi/tags.py so the paths match the project.")
        return 1 if missing else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="opc.tcp://localhost:4840")
    p.add_argument("--user", default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--tree", action="store_true", help="also print the whole tree")
    p.add_argument("--depth", type=int, default=4)
    args = p.parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
