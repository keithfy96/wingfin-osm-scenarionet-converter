# rviz2 for a bag this repo wrote. **A separate image from `metadrive-wingfin-sim`, deliberately.**
#
# A bag is a file. Looking at one needs ROS and a display and nothing else -- no MetaDrive, no
# CUDA, no torch, no 13.3 GB of them. The Stage 10 plan originally scoped this as
# `ros-humble-ros-base` *inside* metadrive-wingfin-sim; that would put a GUI stack behind every
# sweep and
# rebuild the simulator image whenever the viewer changed. This follows what already worked for
# `docs/testing-ros.md` tier 4 instead, where a stock `ros:jazzy-ros-base` read our bag with
# nothing of ours installed.
#
# **jazzy, and humble cannot be used** -- which is the opposite of what the Stage 10 plan assumed.
# `ros_schema` writes message definitions against `Stores.ROS2_HUMBLE`, so matching the viewer to
# humble looks obviously right. It is not: `ros_bag.py` writes rosbag2 **format version 9**, where
# `offered_qos_profiles` is a sequence and topics carry a `type_description_hash`. Humble's
# rosbag2 expects that field to be a *string* and dies on the metadata before it opens the mcap:
#
#     Exception on parsing info file: yaml-cpp: error at line 34, column 29: bad conversion
#
# Line 34 is `offered_qos_profiles: []`. Nothing about the messages is wrong; the container format
# is simply newer than humble reads. Humble also does not ship the mcap storage plugin by default,
# which would have been the next wall. Tier 4's jazzy check was never merely a portability
# nicety -- it is the only distro either half of this has been shown to work on.
FROM osrf/ros:jazzy-desktop

# `vision_msgs` is not in the core distro, and `vision_msgs_rviz_plugins` is what actually draws
# a Detection3DArray. Without the second one rviz2 subscribes and renders nothing, which looks
# exactly like a bag with no objects in it -- and the objects are the whole reason ours is worth
# more than the rig's recording. Both are released for jazzy.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ros-jazzy-vision-msgs \
        ros-jazzy-vision-msgs-rviz-plugins \
    && rm -rf /var/lib/apt/lists/*

# `wingfin_msgs` exists nowhere but this repo. MCAP carries its definition text, which is enough
# for `ros2 bag info` to *list* /perception/traffic_lights and not enough for anything to
# subscribe to it -- that needs generated type support, which needs a built package.
#
# Generated from `ros_schema.EXTRA_DEFINITIONS` at build time rather than checked in beside it:
# the writer registers that dict and rosbag2 writes it into every bag, so a second hand-written
# copy would drift silently -- bags keep carrying the text they were written with while the
# package next to them says something else, and a subscriber deserialises garbage without
# raising. `ros_msgs_package.py` imports only `ros_schema`, which imports only stdlib.
COPY tools/ros_schema.py tools/ros_msgs_package.py /gen/
RUN python3 /gen/ros_msgs_package.py wingfin_msgs /ws/src/wingfin_msgs \
    && . /opt/ros/jazzy/setup.sh \
    && cd /ws \
    && colcon build --packages-select wingfin_msgs \
    && rm -rf /ws/build /ws/log /gen

# rviz2 has no display for `wingfin_msgs/TrafficLightArray` -- no plugin exists for a message
# invented in this repo, so the topic played and drew nothing while `ros2 topic info` reported
# 0 subscribers on it. This translates it to `visualization_msgs/MarkerArray` for display only;
# the bag keeps the real typed topic and the writer is untouched.
COPY docker/ros-viewer/light_markers.py /opt/wingfin/light_markers.py

# Both overlays, or `ros2 bag play` cannot deserialise the two non-core types and rviz2 has no
# plugin for the boxes. The stock image's entrypoint sources only the first.
COPY docker/ros-viewer/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["rviz2"]
