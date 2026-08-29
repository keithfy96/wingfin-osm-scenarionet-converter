"""The IDM policy this repo drives cars with - traffic cars and the ego alike.

    from idm_driving import windowed_policy_class, _window_reference

Split out of `tools/traffic.py` on 2026-08-28, where the class lived inside
`build_manager()` and only traffic cars could reach it. The ego under
`--agent-policy idm` was getting MetaDrive's stock `TrajectoryIDMPolicy`, which is a
different car: flat 40 km/h everywhere, the whole-line projection, and a heading PID whose
integral winds up. All three faults were already found and fixed for traffic; the ego simply
was not on the same code path.

Like `traffic.py`, `drive.py` and `signal_control.py` this imports nothing from the package
and defers its MetaDrive imports into a factory, because it does not run on the same Python:
the repo targets 3.10 and MetaDrive's own venv is 3.8.

**What this fixes that MetaDrive does not.** `IDMPolicy.acceleration` (`idm_policy.py:307`)
is the whole longitudinal law, and road curvature is not an input to it - there is no
curvature-aware speed limiting anywhere in `metadrive/policy/`, and `lane.speed_limit` is
read by nothing but a tollgate reward. So the speed a corner allows has to be handed in from
outside, as `target_speed`; `tools/speed_profile.py` computes it and the callers write it.
"""

import math

import numpy as np


def _window_reference(xy, cumulative, progress, position, back_m, ahead_m):
    """(along, lateral, new_progress) of `position` against the windowed route reference.

    The window is `[progress - back_m, progress + ahead_m]` of arc, so the reference can
    advance along the route but can neither jump a hairpin nor fall onto a parallel leg -
    which the whole-line projection does: 20 of `junction-1`'s routes cross the median gap
    and run back down the opposite carriageway ~8 m away, and a car displaced a couple of
    metres was captured by the wrong leg and drove the median for 20+ s, unculled, because
    the lost test read the same projection. Lateral is positive to the RIGHT of travel,
    matching `InterpolatingLine.local_coordinates` (its lateral direction is the -90 deg
    rotation of the segment), so `steering_control`'s `-lateral` keeps its sign.
    """
    p = np.asarray(position[:2], dtype=float)
    lo = max(int(np.searchsorted(cumulative, progress - back_m)) - 1, 0)
    hi = min(int(np.searchsorted(cumulative, progress + ahead_m)) + 1, len(xy) - 1)
    window = xy[lo:hi + 1]
    offsets = window - p
    j = lo + int(np.argmin(np.einsum("ij,ij->i", offsets, offsets)))
    a = xy[max(j - 1, 0)]
    b = xy[min(j + 1, len(xy) - 1)]
    direction = b - a
    length = float(math.hypot(direction[0], direction[1]))
    if length < 1e-9:
        return progress, 0.0, progress
    direction = direction / length
    offset = p - xy[j]
    along = float(cumulative[j] + direction[0] * offset[0] + direction[1] * offset[1])
    lateral = float(offset[0] * direction[1] - offset[1] * direction[0])
    # Monotone but for a metre of slack: a car nudged backwards must not drag the window
    # with it far enough to reach anything but its own road.
    new_progress = float(np.clip(along, progress - 1.0, progress + ahead_m))
    return along, lateral, new_progress


_POLICY_CLASS = None


def windowed_policy_class():
    """`WindowedTrajectoryIDMPolicy`, built on first call and cached.

    A factory rather than a module-level class for the reason `build_manager` defers its
    own imports: this file has to be readable - and its geometry testable - without
    panda3d, exactly as `signal_control` is.
    """
    global _POLICY_CLASS
    if _POLICY_CLASS is not None:
        return _POLICY_CLASS

    from metadrive.component.vehicle.PID_controller import PIDController
    from metadrive.policy.idm_policy import FrontBackObjects, TrajectoryIDMPolicy
    from metadrive.utils.math import wrap_to_pi

    class WindowedTrajectoryIDMPolicy(TrajectoryIDMPolicy):
        """`TrajectoryIDMPolicy` that tracks a windowed reference and tracks a speed.

        Three departures from the stock class, each measured before it was kept.

        **1. The reference point can only move along the route.** The stock policy projects
        the car onto the WHOLE trajectory every step
        (`InterpolatingLine.local_coordinates`), and 20 of `junction-1`'s 60 traffic routes
        cross the median gap at (-65, 73) and then run back down the parallel carriageway
        ~8 m away. A car displaced a couple of metres sideways - a give-way stop, a nudge,
        the crossover turn itself - is then captured by the *other* leg: the projection
        reads a small lateral against the wrong piece of road, the heading target flips,
        and the car settles into driving the median grass or the oncoming carriageway.
        Indefinitely: the lost-car test reads the same projection, so it never fires.
        Measured on three recorded 25-car episodes: single cars 20+ s off the road at
        (-64, 23) and (-58, -52), 2.2-3.0 m from their own line, entering at 14-31 km/h.
        Driven solo with the reference windowed, the same routes track their tightest
        bends - 2.4 and 3.3 m of radius - to 0.25 m.

        The reference is the nearest route point within `BACK_M` behind and `AHEAD_M`
        ahead of the last reference, and progress is clamped to that window, so it can
        neither jump a hairpin nor fall onto a parallel leg. `steering_control` is
        otherwise the stock arithmetic - same PIDs, same 1 m lookahead.

        **2. The heading PID keeps the stock gains with no integral.** `PIDController`
        accumulates `i_error` for ever and never resets it, and a car creeping through a
        tight hook at 1-3 km/h holds a heading error for hundreds of steps while barely
        moving - the integral winds up into a standing steering bias that the lateral term
        (kp 0.3, no integral of its own) can only balance at a constant offset. Measured:
        cars settling 2.5-4.9 m beside their own route for 20-30 s, lateral read correctly
        the whole time.

        **3. The longitudinal command is a tracker rather than a relay.** See `DELTA` and
        `act` below.
        """

        BACK_M = 5.0
        AHEAD_M = 12.0

        DELTA = 4.0
        """Exponent of the velocity term in `acceleration`, against MetaDrive's 10.

        `1 - (v/target)^DELTA` at DELTA 10 is not a controller, it is a relay: 10% over the
        target asks for -1.59, which the action clamp (`base_vehicle.py:203-208`) makes a
        full brake, and 10% under asks for +0.65, two thirds of full throttle. There is no
        proportional band, so a car handed a corner speed of 4-8 km/h by the profile can
        only oscillate between stopped and over - and full engine force is 4 x 800 N on
        1100 kg, about 2.9 m/s2, so a single decision moves the speed further than the
        target itself. At 4.0 the same +-10% asks for -0.46 and +0.34.

        4.0 is not invented here: it is highway-env's own figure, and MetaDrive still
        carries `DELTA_RANGE = [3.5, 4.5]` two lines under the 10 it ships
        (`idm_policy.py:202-206`).
        """

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.heading_pid = PIDController(1.2, 0.0, 3.5)
            self._route_xy = None
            self._route_arc = None
            self._progress = 0.0
            self._front_obj = None

        def prime(self, xy, cumulative, start_arc):
            """Give the policy its route as arrays, and tell it where the car starts."""
            self._route_xy = np.asarray(xy, dtype=float)
            self._route_arc = np.asarray(cumulative, dtype=float)
            self._progress = float(start_arc)

        def _route_arrays(self):
            """The windowing arrays, derived from the trajectory if nobody primed us.

            `tools/traffic.py` primes every car it spawns, because it holds the localised
            route already. The ego has no manager to do that, so it falls back to the
            `PointLane` MetaDrive built for it - `InterpolatingLine` decimates to about a
            metre, which is the window's own resolution and finer than it needs.
            """
            if self._route_xy is None:
                segments = self.traj_to_follow.segment_property
                points = [seg["start_point"] for seg in segments]
                points.append(segments[-1]["end_point"])
                xy = np.asarray(points, dtype=float)[..., :2]
                steps = np.hypot(*np.diff(xy, axis=0).T)
                self._route_xy = xy
                self._route_arc = np.concatenate(([0.0], np.cumsum(steps)))
                self._progress = 0.0
            return self._route_xy, self._route_arc

        def route_coordinates(self, position):
            """(along, lateral) against the windowed reference. See `_window_reference`."""
            xy, cumulative = self._route_arrays()
            along, lateral, self._progress = _window_reference(
                xy, cumulative, self._progress, position, self.BACK_M, self.AHEAD_M,
            )
            return along, lateral

        def steering_control(self, target_lane) -> float:
            ego = self.control_object
            along, lateral = self.route_coordinates(ego.position)
            lane_heading = target_lane.heading_theta_at(along + 1)
            steering = self.heading_pid.get_result(-wrap_to_pi(lane_heading - ego.heading_theta))
            steering += self.lateral_pid.get_result(-lateral)
            return float(steering)

        def _front_distance(self):
            """Metres to the cached front object, or None if there is no usable one.

            Planar rather than longitudinal, and every step rather than on the search step
            alone. `get_find_front_back_objs_single_lane` measures along the lane, which
            costs a whole-line `local_coordinates` per object; the chord is within a few
            per cent of it inside the 20 m `IDM_MAX_DIST` window and errs short, which
            brakes slightly sooner. Using it on *every* step - the search step included -
            is what keeps the gap term continuous instead of stepping when the search runs.
            """
            if self._front_obj is None:
                return None
            try:
                here = self.control_object.position
                there = self._front_obj.position
            except Exception:
                # The object was cleared between the search and now - a retired car, a
                # light that went out. Forget it rather than brake for a ghost.
                self._front_obj = None
                return None
            return float(math.hypot(there[0] - here[0], there[1] - here[1]))

        def act(self, do_speed_control=True, *args, **kwargs):
            """Steer every step, search for a car in front one step in five, brake live.

            The stock `act` (`idm_policy.py:475-503`) latches the whole acceleration on the
            steps it does not run the search: `acc = self.last_action[-1]`. With
            `IDM_ACT_BATCH_SIZE` at 5 that holds one saturated pedal for 0.4 s at 10 Hz,
            which at corner speeds is longer than it takes to stop the car - and once
            `speed_in_heading` drops under `base_vehicle.py`'s 0.01 m/s DEADZONE the brake
            latches hard and the car is stationary until the policy commands a positive
            throttle. That is the start-stop.

            What is expensive is `lidar.get_surrounding_objects` and the lane test in
            `get_find_front_back_objs_single_lane`, not the arithmetic. So the *search* is
            still staggered and the *command* is not: the front object is remembered, and
            `acceleration` is recomputed every step against the live speed and the live
            distance to it.

            `do_speed_control` is a bool from `tools/traffic.py`, and the agent id string
            from `agent_manager.py:189` when this drives the ego - both truthy the same way.
            """
            try:
                if do_speed_control:
                    objects = self.control_object.lidar.get_surrounding_objects(self.control_object)
                    surrounding = FrontBackObjects.get_find_front_back_objs_single_lane(
                        objects,
                        self.routing_target_lane,
                        self.control_object.position,
                        max_distance=self.IDM_MAX_DIST,
                    )
                    self._front_obj = surrounding.front_object()
                distance = self._front_distance()
                acc = self.acceleration(self._front_obj if distance is not None else None, distance)
            except Exception:
                acc = 0.0
                self._front_obj = None
                print(
                    "TrajectoryIDM Policy longitudinal planning failed, "
                    "acceleration fall back to 0"
                )

            steering = self.steering_control(self.routing_target_lane)
            self.last_action = [steering, acc]
            action = [steering, acc]
            self.action_info["action"] = action
            return action

    _POLICY_CLASS = WindowedTrajectoryIDMPolicy
    return _POLICY_CLASS
