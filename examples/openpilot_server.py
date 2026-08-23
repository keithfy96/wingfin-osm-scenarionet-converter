"""Put wing-sim's openpilot bridge behind the drive's `--policy-url`.

    python examples/openpilot_server.py --backend stub --port 8642
    # then, from inside scripts/:
    ./drive.sh junction-1 -- --agent-policy remote --policy-url http://127.0.0.1:8642 \
        --sensors imu,route --render none

This is `examples/policy_server.py` with one particular model behind `act` instead of a slot
to edit. It speaks the same three endpoints, so `--policy-url` and `step-timing.sh --rows 3`
reach it unchanged; the translation to the bridge's own protocol is `tools/openpilot_policy.py`,
which is where every fact about that protocol is written down.

Unlike `examples/policy_server.py` this file does import from the repo - it is a specific
adapter rather than the template - but the transport half is deliberately the same code shape,
because that file is the one people already run.

Two backends:

    stub      a real socket speaking the real protocol with a pure-pursuit law behind it.
              No fork, no Docker, no SSH key. This is what proves the frame, the two sign
              conventions and the round trip before there is a bridge to blame.
    bridge    the real thing, at --bridge HOST:PORT. Needs `wing-sim/openpilot/pull.sh` to
              have run, which needs access to the private zapetaai fork.

**The drive must send `--sensors imu,route`.** The bridge is a controller: it wants a path
and three ego scalars, and neither is in the 161-float observation in a usable form - the
observation's own navigation block is normalised and clipped at 30 m, which cannot be undone.
`route` is the recorded route in metres, straight off the object `TrajectoryNavigation` steers
by.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "tools"))

from openpilot_policy import (  # noqa: E402
    DEFAULT_STEER_RATIO,
    LONGITUDINAL_MODES,
    BridgeError,
    OpenpilotDriver,
    StubBridge,
)
from pedal_map import DEFAULT_PEDAL_MAP  # noqa: E402


def build_handler(driver, telemetry):
    """One handler class, closed over the driver and the optional telemetry sink."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # The other half of the 41 ms -> 0.126 ms round trip; `tools/policy_client.py` sets
        # TCP_NODELAY on its end. Miss either and every step costs 40 ms of pure waiting.
        disable_nagle_algorithm = True

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

            try:
                if route == "spec":
                    for note in driver.spec(payload):
                        print(f"note         {note}", flush=True)
                    self._reply({"ok": True})
                    return

                if route == "episode":
                    sent = driver.episode(payload)
                    print(
                        "episode      {}  max_steer {:.1f} deg  wheelbase {:.2f} m  "
                        "bridge ready".format(
                            payload.get("scenario_id", ""),
                            sent["max_steer_angle"],
                            sent["wheelbase_m"],
                        ),
                        flush=True,
                    )
                    self._reply({"ok": True})
                    return

                if route == "act":
                    action = driver.act(
                        payload.get("observation"), payload.get("sensors") or {}, None
                    )
                    if telemetry is not None:
                        # The bridge's own reply at the top level, so a grep for one of its
                        # forty fields still works, plus the two things only this side knows:
                        # the speed the pedal was chosen at and the pedal that was chosen.
                        # Without them "how often did a request to slow down come back as
                        # throttle" cannot be answered from the log, and that question is the
                        # whole reason --longitudinal has three values.
                        record = dict(driver.last_reply)
                        record["v_ego_mps"] = driver.last_v_ego
                        record["metadrive_action"] = [float(action[0]), float(action[1])]
                        telemetry.write(json.dumps(record) + "\n")
                    self._reply({"action": [float(action[0]), float(action[1])]})
                    return
            except BridgeError as error:
                # 500 rather than a silent zero action: `RemotePolicy` turns a non-200 into a
                # PolicyError naming this endpoint, which stops the drive with the reason
                # instead of letting a coasting car read as a badly tuned controller.
                print(f"error        {error}", flush=True)
                self._reply({"error": str(error)}, status=500)
                return

            self._reply({"error": "unknown endpoint " + self.path}, status=404)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument(
        "--backend",
        default="stub",
        choices=["stub", "bridge"],
        help="`stub` needs nothing installed. `bridge` needs the openpilot fork running.",
    )
    parser.add_argument(
        "--bridge",
        default="127.0.0.1:5558",
        help="--backend bridge: where the openpilot bridge is listening",
    )
    parser.add_argument(
        "--target-speed-mps",
        type=float,
        default=10.0,
        help="The bridge reads a missing target as 0.0, which is a stop, so one is sent every "
        "tick. 10 m/s is 36 km/h, under junction-1's posted 50.",
    )
    parser.add_argument(
        "--steer-ratio",
        type=float,
        default=DEFAULT_STEER_RATIO,
        help="Must match the fork's own CP.steerRatio, which the bridge divides by whatever "
        "init says. A mismatch mis-reports the wheel angle rather than changing the output.",
    )
    parser.add_argument(
        "--longitudinal",
        default="pedal",
        choices=list(LONGITUDINAL_MODES),
        help="`table` looks `accel_cmd` up in a pedal map measured on MetaDrive's own car by "
        "tools/pedal_sweep.py, and is the only one of the three that is a calibration. "
        "`pedal` takes the bridge's own throttle/brake, which come from a CARLA pedal map "
        "whose zero crossing is near -1.6 m/s2 - so a gentle-braking request arrives here as "
        "a fifth of full throttle. `accel` normalises `accel_cmd` by the Tesla envelope: not "
        "calibrated either, but sign-correct. Only `pedal` works against --backend stub, "
        "which answers in pedals and carries no accel_cmd.",
    )
    parser.add_argument(
        "--pedal-map",
        default=DEFAULT_PEDAL_MAP,
        help=f"--longitudinal table: the measured table (default: {DEFAULT_PEDAL_MAP}). "
        "Read only in that mode, so the other two need no file.",
    )
    parser.add_argument(
        "--log-telemetry",
        default=None,
        help="Write one JSON object per step to this .jsonl: the bridge's whole reply - about "
        "forty diagnostic fields covering the MPC solution and the longitudinal state - plus "
        "`v_ego_mps` and the `metadrive_action` this side turned it into.",
    )
    arguments = parser.parse_args()

    stub = None
    if arguments.backend == "stub":
        stub = StubBridge()
        bridge_host, bridge_port = stub.host, stub.port
        print(f"backend      stub bridge on {bridge_host}:{bridge_port}", flush=True)
    else:
        bridge_host, _, port_text = arguments.bridge.rpartition(":")
        if not bridge_host:
            parser.error("--bridge wants HOST:PORT")
        bridge_port = int(port_text)
        print(f"backend      openpilot bridge at {bridge_host}:{bridge_port}", flush=True)

    try:
        driver = OpenpilotDriver(
            bridge_host,
            bridge_port,
            target_speed_mps=arguments.target_speed_mps,
            steer_ratio=arguments.steer_ratio,
            longitudinal=arguments.longitudinal,
            # Only in `table` mode, so a missing file is not an error for the other two - and
            # the default path means `--longitudinal table` alone is enough once swept.
            pedal_map=(
                arguments.pedal_map if arguments.longitudinal == "table" else None
            ),
        )
    except BridgeError as error:
        # Before the socket is bound, so a bad table stops here rather than on the first step
        # of a drive that has already built a map.
        print(f"error        {error}", flush=True)
        if stub is not None:
            stub.close()
        return 2
    if driver.pedal_map is not None:
        print(f"pedal map    {driver.pedal_map.summary()}", flush=True)
    # Held open for the life of the server and closed in `finally`; a context manager here
    # would have to wrap `serve_forever`, which is the rest of the function.
    telemetry = (
        open(arguments.log_telemetry, "w")  # noqa: SIM115
        if arguments.log_telemetry
        else None
    )

    server = ThreadingHTTPServer(
        (arguments.host, arguments.port), build_handler(driver, telemetry)
    )
    print(
        f"listening    http://{arguments.host}:{arguments.port}  "
        f"target {arguments.target_speed_mps:.1f} m/s",
        flush=True,
    )
    print(
        f"drive with   --agent-policy remote --policy-url "
        f"http://{arguments.host}:{arguments.port} --sensors imu,route",
        flush=True,
    )

    # `serve_forever` polls, and a signal arriving inside that poll does not reliably surface
    # as KeyboardInterrupt without a controlling terminal - `examples/policy_server.py`
    # measured `kill -INT` leaving the process serving. `shutdown` is called from another
    # thread because calling it from the handler deadlocks against the loop it is stopping.
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
        driver.close()
        if stub is not None:
            stub.close()
        if telemetry is not None:
            telemetry.close()
            print(f"telemetry    -> {arguments.log_telemetry}", flush=True)
        print(f"steps        {driver.bridge.steps}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
