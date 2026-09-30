"""Entry point: connect to the CODESYS OPC UA server and serve the HMI.

    python -m cashmi --url opc.tcp://localhost:4840
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import webbrowser

from . import tags as T
from .opc import Collector, History
from .web import serve

DEFAULT_URL = "opc.tcp://localhost:4840"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=DEFAULT_URL,
                   help=f"CODESYS OPC UA endpoint (default: {DEFAULT_URL})")
    p.add_argument("--host", default="127.0.0.1", help="HMI bind address")
    p.add_argument("--port", type=int, default=8080, help="HMI port")
    p.add_argument("--period", type=float, default=T.FAST_PERIOD_S,
                   help="PLC poll period in seconds")
    p.add_argument("--user", default=None, help="OPC UA user name, if required")
    p.add_argument("--password", default=None, help="OPC UA password, if required")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


async def amain(args) -> int:
    history = History()
    server = serve(history, args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    logging.info("HMI on %s", url)
    logging.info("fast trend %d s (10 x tau_level %.1f s), "
                 "slow trend %.1f h (10 x tau_oxygen %.0f min)",
                 T.FAST_WINDOW_S, T.TAU_LEVEL_S,
                 T.SLOW_WINDOW_S / 3600, T.TAU_OXYGEN_S / 60)

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - headless is fine
            pass

    collector = Collector(args.url, history, period=args.period,
                          user=args.user, password=args.password)
    try:
        await collector.run()
    except asyncio.CancelledError:
        pass
    finally:
        collector.stop()
        server.shutdown()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(name)-12s %(levelname)-7s %(message)s",
    )
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
