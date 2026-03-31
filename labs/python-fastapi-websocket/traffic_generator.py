"""
Traffic Generator for the FastAPI WebSocket OTEL Demo.

Simulates multiple concurrent WebSocket clients that connect, exchange
messages, and disconnect — generating a steady stream of traces and
metrics visible in Splunk Observability Cloud.

Usage:
    python traffic_generator.py                      # defaults: 5 clients, 60s
    python traffic_generator.py --clients 10 --duration 120
    python traffic_generator.py --url ws://myhost:8000/ws
"""

from __future__ import annotations

import argparse
import asyncio
import random
import string
import time
import sys

try:
    import websockets
except ImportError:
    print("❌  'websockets' package required.  pip install websockets")
    sys.exit(1)


# ── Sample data for realistic-looking messages ───────────────────────
SAMPLE_MESSAGES = [
    "Hey, anyone online?",
    "Just deployed a new version 🚀",
    "Checking latency on this WebSocket connection",
    "Can someone review my PR?",
    "The dashboard looks great!",
    "Running a load test right now",
    "How's the CPU utilization looking?",
    "Traces are flowing into Splunk 🎉",
    "Let me check the error rate…",
    "WebSocket keep-alive ping",
    "Metric export interval is 10s",
    "OpenTelemetry is awesome",
    "Broadcast storm incoming!",
    "Testing reconnection logic",
    "Hello from the traffic generator",
    "gRPC exporter is connected",
    "Latency histogram looks normal",
    "Span attributes are populated correctly",
    "Active connections gauge is updating",
    "Simulating real user chat behaviour",
]


def random_client_id() -> str:
    """Generate a random client ID like 'bot-x7k2m'."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"bot-{suffix}"


async def simulate_client(
    ws_base_url: str,
    client_id: str,
    duration: float,
    msg_delay_range: tuple[float, float],
    stats: dict,
):
    """
    A single simulated WebSocket client.

    Connects, sends random messages at random intervals, and disconnects
    either when duration expires or after a random session length.
    """
    url = f"{ws_base_url}/{client_id}"
    session_length = random.uniform(duration * 0.3, duration)  # vary session length
    start = time.monotonic()

    try:
        async with websockets.connect(url) as ws:
            stats["connections"] += 1
            print(f"  ✅  {client_id} connected")

            while (time.monotonic() - start) < session_length:
                # Send a message
                msg = random.choice(SAMPLE_MESSAGES)
                await ws.send(msg)
                stats["messages_sent"] += 1

                # Read any incoming messages (non-blocking drain)
                try:
                    while True:
                        await asyncio.wait_for(ws.recv(), timeout=0.1)
                        stats["messages_received"] += 1
                except (asyncio.TimeoutError, websockets.ConnectionClosed):
                    pass

                # Random delay between messages
                delay = random.uniform(*msg_delay_range)
                await asyncio.sleep(delay)

            # Graceful close
            await ws.close()
            stats["disconnections"] += 1
            print(f"  👋  {client_id} disconnected (session {session_length:.1f}s)")

    except websockets.ConnectionClosed:
        stats["disconnections"] += 1
        print(f"  ⚠️  {client_id} connection closed unexpectedly")
    except Exception as exc:
        stats["errors"] += 1
        print(f"  ❌  {client_id} error: {exc}")


async def run_traffic(
    ws_base_url: str,
    num_clients: int,
    duration: float,
    msg_delay_range: tuple[float, float],
    stagger: float,
):
    """Launch multiple simulated clients with staggered start times."""
    stats = {
        "connections": 0,
        "disconnections": 0,
        "messages_sent": 0,
        "messages_received": 0,
        "errors": 0,
    }

    print(f"\n🔄  Starting traffic generator")
    print(f"    URL:       {ws_base_url}")
    print(f"    Clients:   {num_clients}")
    print(f"    Duration:  {duration}s")
    print(f"    Msg delay: {msg_delay_range[0]:.1f}–{msg_delay_range[1]:.1f}s")
    print(f"    Stagger:   {stagger:.1f}s between client launches\n")

    tasks: list[asyncio.Task] = []
    start = time.monotonic()

    for i in range(num_clients):
        cid = random_client_id()
        task = asyncio.create_task(
            simulate_client(ws_base_url, cid, duration, msg_delay_range, stats)
        )
        tasks.append(task)

        # Stagger client connections so they don't all hit at once
        if i < num_clients - 1:
            await asyncio.sleep(stagger)

    # Wait for all clients to finish
    await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.monotonic() - start

    print(f"\n{'─' * 50}")
    print(f"📊  Traffic Generation Summary")
    print(f"{'─' * 50}")
    print(f"    Duration:           {elapsed:.1f}s")
    print(f"    Connections:        {stats['connections']}")
    print(f"    Disconnections:     {stats['disconnections']}")
    print(f"    Messages sent:      {stats['messages_sent']}")
    print(f"    Messages received:  {stats['messages_received']}")
    print(f"    Errors:             {stats['errors']}")
    print(f"{'─' * 50}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generate WebSocket traffic for the FastAPI OTEL demo"
    )
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/ws",
        help="Base WebSocket URL (default: ws://localhost:8000/ws)",
    )
    parser.add_argument(
        "--clients",
        type=int,
        default=5,
        help="Number of concurrent simulated clients (default: 5)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=60,
        help="Total run duration in seconds (default: 60)",
    )
    parser.add_argument(
        "--min-delay",
        type=float,
        default=0.5,
        help="Minimum seconds between messages per client (default: 0.5)",
    )
    parser.add_argument(
        "--max-delay",
        type=float,
        default=3.0,
        help="Maximum seconds between messages per client (default: 3.0)",
    )
    parser.add_argument(
        "--stagger",
        type=float,
        default=1.0,
        help="Seconds between launching each client (default: 1.0)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_traffic(
                ws_base_url=args.url,
                num_clients=args.clients,
                duration=args.duration,
                msg_delay_range=(args.min_delay, args.max_delay),
                stagger=args.stagger,
            )
        )
    except KeyboardInterrupt:
        print("\n\n⏹  Traffic generator stopped by user (Ctrl+C).\n")


if __name__ == "__main__":
    main()
