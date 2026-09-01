"""Republish `/perception/traffic_lights` as markers, so rviz2 can draw them.

**Display only, and it lives in the viewer image rather than in the writer.** The bag carries
`wingfin_msgs/TrafficLightArray` - a real typed message with an id, a status word and a position -
because that is what a consumer should receive. rviz2 cannot draw it: there is no display plugin
for a message type invented in this repo, so the topic played and rendered nothing, and
`ros2 topic info` reported **0 subscribers** on the one topic a person most wants to see.

Writing an rviz plugin is a C++ package. Republishing as `visualization_msgs/MarkerArray` is
twenty lines and costs the bag nothing - the typed topic stays exactly as recorded, and this
translation exists only inside the viewer.

The colour is the point. A traffic light whose state is a *word* in the data and a *colour* on
screen is checkable at a glance in a way no number is: two conflicting approaches showing green
at once is a broken signal plan, and nothing in `ros_probe.py` can see it, because the bag does
not carry which movements conflict.
"""

import contextlib

import rclpy
from rclpy.node import Node
from visualization_msgs.msg import Marker, MarkerArray
from wingfin_msgs.msg import TrafficLightArray

#: MetaDrive's own `MetaDriveType.LIGHT_*` words, passed through the whole pipeline unaltered so
#: the dataset, the bag and this all say the same thing. Grey for anything unrecognised, rather
#: than a default colour that would quietly render an unknown state as a working one.
COLOURS = {
    "TRAFFIC_LIGHT_GREEN": (0.1, 0.9, 0.1),
    "TRAFFIC_LIGHT_YELLOW": (0.95, 0.85, 0.1),
    "TRAFFIC_LIGHT_RED": (0.9, 0.1, 0.1),
    "TRAFFIC_LIGHT_UNKNOWN": (0.5, 0.5, 0.5),
}
UNRECOGNISED = (0.5, 0.5, 0.5)

SPHERE_M = 2.0
"""Bigger than a real lamp. These mark a junction seen from 120 m up, not a light to scale."""

LABEL_HEIGHT_M = 3.0


class LightMarkers(Node):
    def __init__(self):
        super().__init__("wingfin_light_markers")
        self.publisher = self.create_publisher(
            MarkerArray, "/perception/traffic_lights/markers", 10
        )
        self.create_subscription(
            TrafficLightArray, "/perception/traffic_lights", self.republish, 10
        )

    def republish(self, incoming):
        markers = MarkerArray()
        for index, light in enumerate(incoming.lights):
            red, green, blue = COLOURS.get(light.status, UNRECOGNISED)

            lamp = Marker()
            lamp.header = incoming.header
            lamp.ns = "traffic_lights"
            # Stable per light, so rviz updates a marker in place rather than accumulating one
            # per frame. Two ids each: the lamp and the word above it.
            lamp.id = index * 2
            lamp.type = Marker.SPHERE
            lamp.action = Marker.ADD
            lamp.pose.position = light.position
            lamp.pose.orientation.w = 1.0
            lamp.scale.x = lamp.scale.y = lamp.scale.z = SPHERE_M
            lamp.color.r, lamp.color.g, lamp.color.b, lamp.color.a = red, green, blue, 0.9
            markers.markers.append(lamp)

            label = Marker()
            label.header = incoming.header
            label.ns = "traffic_lights"
            label.id = index * 2 + 1
            label.type = Marker.TEXT_VIEW_FACING
            label.action = Marker.ADD
            label.pose.position.x = light.position.x
            label.pose.position.y = light.position.y
            label.pose.position.z = light.position.z + LABEL_HEIGHT_M
            label.pose.orientation.w = 1.0
            label.scale.z = 1.5
            label.color.r, label.color.g, label.color.b, label.color.a = red, green, blue, 1.0
            label.text = light.status.replace("TRAFFIC_LIGHT_", "")
            markers.markers.append(label)

        self.publisher.publish(markers)


def main():
    rclpy.init()
    node = LightMarkers()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        with contextlib.suppress(Exception):
            rclpy.shutdown()


if __name__ == "__main__":
    main()
