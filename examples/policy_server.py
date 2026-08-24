"""Host a driving model in your own process. Edit `act` and run this on any interpreter.

    python examples/policy_server.py --port 8642
    # then, in another terminal:
    ./scripts/drive.sh junction-1 -- --agent-policy remote --policy-url http://127.0.0.1:8642

This file imports nothing from this repo and nothing that is not in the standard library, so
it runs on **whatever python your model runs on** - which is the point of it. MetaDrive's own
venv is Python 3.8.20 with no torch; yours is presumably not.

The contract is two floats:

    observation (161 numbers in [0, 1])  ->  [steering, throttle_brake] (both in [-1, 1])

`steering` is [-1, 1] of full lock. `throttle_brake` is engine force when >= 0 and **braking
when < 0** - a model whose output is [0, 1] cannot brake at all, and that reads as timid
driving rather than as a bug. `tools/policy_client.py` refuses an action outside the range
instead of letting MetaDrive clip it silently.

Run `--backend` for the stand-ins that prove the wire before a model is put behind it:

    zero        [0, 0] every step
    constant    a fixed action, from --steering / --throttle
    replay      actions read in order from an .npz recorded by `drive.py --record`
    edit-me     whatever `act` below returns (the default)

`replay` is the one that matters. Feeding back the actions `IdmDriver` produced locally must
reproduce that drive exactly - same step count, same route completion, bit-identical arrays -
which proves the observation goes out and the action comes back with nothing lost in between.
It needs no model, and until it passes there is no point blaming one.
"""

from __future__ import annotations

import argparse
import base64
import json
import signal
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------------------
# EDIT HERE. This is the whole of the model's side of the socket.
# ---------------------------------------------------------------------------------------


def act(observation, sensors, spec):
    """Return `[steering, throttle_brake]`, both in [-1, 1].

    `observation` is a list of floats - 161 of them by default, laid out as `spec["observation"]
    ["layout"]` describes. What is worth knowing about it, measured on junction-1 rather than
    assumed:

    * `[0:12]` **side detector** - 12 lasers against the static world. These are what see the
      road edges, and they move.
    * `[19:41]` **navigation** - the next 10 route points as (ahead, sideways) in the car's own
      frame, clipped at 30 m. This is where the route is.
    * `[41:161]` **ray lidar** - 120 lasers, and **every one of them is 1.0 today**, because
      that sensor scans the *dynamic* world and our scenarios hold one car. It will start
      carrying something when traffic does.

    `sensors` holds whatever `--sensors` asked for: `imu` and `gps` as plain numbers, and
    `camera`, `depth`, `semantic`, `point-cloud` as `{"dtype", "shape", "b64"}`, which
    `decode_array` below turns back into an array.

    **Read the dtype rather than assuming one.** `camera` and `semantic` arrive as **uint8
    0-255**, which is what the GPU produced; a model wanting 0-1 floats does that divide
    itself, fused with whatever channel order and transpose its weights expect. `depth` and
    `point-cloud` arrive as float32 and are not pictures - depth is a nonlinear 0-1 buffer
    and the point cloud is in metres - so neither is 8-bit at any point.

    `spec` is what the drive sent once at the start - the layout, the action range, the step
    length, and the dataset's projection.
    """
    # A placeholder that drives nowhere. Replace it.
    del observation, sensors, spec
    return [0.0, 0.0]


# ---------------------------------------------------------------------------------------
# Below here is transport, and none of it needs changing.
# ---------------------------------------------------------------------------------------


def decode_array(encoded):
    """`{"dtype", "shape", "b64"}` back into nested lists, without needing numpy.

    Uses numpy when it is importable, because a model's process almost certainly has it, and
    falls back to `struct` so that this file's promise of "standard library only" is true.
    """
    raw = base64.b64decode(encoded["b64"])
    try:
        import numpy

        return numpy.frombuffer(raw, dtype=encoded["dtype"]).reshape(encoded["shape"])
    except ImportError:
        code = {"float32": "f", "float64": "d", "int64": "q", "int32": "i", "uint8": "B"}[
            encoded["dtype"]
        ]
        count = len(raw) // struct.calcsize(code)
        flat = list(struct.unpack(f"<{count}{code}", raw))
        for size in reversed(encoded["shape"][1:]):
            flat = [flat[i : i + size] for i in range(0, len(flat), size)]
        return flat


def _flat(observation):
    """The observation as a plain list of floats, whatever shape it arrived in."""
    if observation is None:
        return []
    if isinstance(observation, dict) and "b64" in observation:
        values = decode_array(observation)
        return [float(v) for v in getattr(values, "ravel", lambda: values)()]
    if isinstance(observation, dict):
        # An image observation: {"image": ..., "state": ...}. The state is the numeric half.
        return _flat(observation.get("state"))
    return [float(v) for v in observation]


def _backend(arguments):
    """The stand-in `act` selected by --backend, or the real one."""
    if arguments.backend == "edit-me":
        return act
    if arguments.backend == "zero":
        return lambda observation, sensors, spec: [0.0, 0.0]
    if arguments.backend == "constant":
        fixed = [float(arguments.steering), float(arguments.throttle)]
        return lambda observation, sensors, spec: list(fixed)

    import numpy

    recorded = numpy.load(arguments.replay_from)["actions"]
    print(
        f"backend      replay: {recorded.shape} actions from {arguments.replay_from}",
        flush=True,
    )

    def replay(observation, sensors, spec):
        index = min(replay.step, len(recorded) - 1)
        replay.step += 1
        return [float(value) for value in recorded[index]]

    replay.step = 0
    return replay


def build_handler(driver, log_path):
    """One handler class, closed over the chosen backend."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # The other half of the 41 ms -> 0.126 ms round trip; `tools/policy_client.py` sets
        # TCP_NODELAY on its end. Miss either and Nagle meets delayed ACK and every step costs
        # 40 ms of pure waiting, which reads as a slow simulator.
        disable_nagle_algorithm = True
        spec = {}

        def log_message(self, *args):
            pass  # one line per step at 10 Hz is not a log, it is a flood

        def _reply(self, payload, status=200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's own naming
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            route = self.path.rstrip("/").rsplit("/", 1)[-1]

            if route == "spec":
                Handler.spec = payload
                sensors = ", ".join(payload.get("sensors") or []) or "none"
                print(
                    "spec         observation {} wide, sensors: {}".format(
                        sum(block["size"] for block in payload["observation"]["layout"]), sensors
                    ),
                    flush=True,
                )
                self._reply({"ok": True})
                return

            if route == "episode":
                print("episode      {}".format(payload.get("scenario_id", "")), flush=True)
                for attribute in ("step",):
                    if hasattr(driver, attribute):
                        setattr(driver, attribute, 0)
                self._reply({"ok": True})
                return

            if route == "act":
                observation = _flat(payload.get("observation"))
                if log_path is not None:
                    received.append(observation)
                action = driver(observation, payload.get("sensors") or {}, Handler.spec)
                self._reply({"action": [float(action[0]), float(action[1])]})
                return

            self._reply({"error": "unknown endpoint " + self.path}, status=404)

    Handler.received = received
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument(
        "--backend",
        default="edit-me",
        choices=["edit-me", "zero", "constant", "replay"],
        help="`edit-me` calls act() above. The other three are stand-ins for testing the wire.",
    )
    parser.add_argument("--steering", type=float, default=0.0, help="--backend constant")
    parser.add_argument("--throttle", type=float, default=0.0, help="--backend constant")
    parser.add_argument(
        "--replay-from", default=None, help="--backend replay: an .npz from drive.py --record"
    )
    parser.add_argument(
        "--log-observations",
        default=None,
        help="Write every observation received to this .npy on shutdown. Compared against the "
        "drive's own --record output, this is what proves nothing was lost on the wire.",
    )
    arguments = parser.parse_args()

    if arguments.backend == "replay" and not arguments.replay_from:
        parser.error("--backend replay needs --replay-from PATH.npz")

    driver = _backend(arguments)
    handler = build_handler(driver, arguments.log_observations)
    server = ThreadingHTTPServer((arguments.host, arguments.port), handler)
    print(
        f"listening    http://{arguments.host}:{arguments.port}  backend {arguments.backend}",
        flush=True,
    )

    # `serve_forever` polls, and a signal arriving while it is inside that poll does not
    # reliably surface as KeyboardInterrupt when this runs without a controlling terminal -
    # measured: `kill -INT` left the process serving. So both signals are handled explicitly,
    # and `shutdown` is called from another thread because calling it from the handler would
    # deadlock against the loop it is stopping. Without this, --log-observations never writes.
    def stop(signal_number, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("", flush=True)
    finally:
        server.server_close()
        if arguments.log_observations and handler.received:
            import numpy

            numpy.save(arguments.log_observations, numpy.asarray(handler.received, dtype="float32"))
            print(
                f"received     {len(handler.received)} observations "
                f"-> {arguments.log_observations}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
