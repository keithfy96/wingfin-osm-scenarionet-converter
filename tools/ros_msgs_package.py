"""Emit a colcon package for the message types this repo invents, from the text it writes.

    uv run python tools/ros_msgs_package.py wingfin_msgs build/wingfin_msgs

`ros_schema.EXTRA_DEFINITIONS` is the `.msg` text `ros_bag.py` registers with `rosbags` and
rosbag2 writes into every bag. That text is enough for a *reader* - MCAP carries it, which is why
a stock `ros:jazzy-ros-base` lists `wingfin_msgs/msg/TrafficLightArray` with no package of ours
installed anywhere. It is **not** enough for a subscriber: rviz2, or anything that wants the
messages as objects, needs generated type support, which means a built package.

**Generated rather than checked in, on purpose.** A hand-written copy of these `.msg` files
beside the dict would be two sources for one definition, and the failure is silent in the worst
way: bags keep carrying the text they were written with while the package built next to them says
something else, and a subscriber deserialises garbage without raising. There is one definition
here and everything is derived from it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ros_schema

PACKAGE_XML = """<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>{package}</name>
  <version>0.0.0</version>
  <description>{description}</description>
  <maintainer email="noreply@example.com">wingfin</maintainer>
  <license>Proprietary</license>

  <buildtool_depend>ament_cmake</buildtool_depend>
  <buildtool_depend>rosidl_default_generators</buildtool_depend>
{depends}
  <exec_depend>rosidl_default_runtime</exec_depend>
  <member_of_group>rosidl_interface_packages</member_of_group>

  <export>
    <build_type>ament_cmake</build_type>
  </export>
</package>
"""

CMAKELISTS = """cmake_minimum_required(VERSION 3.8)
project({package})

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
{finds}
rosidl_generate_interfaces(${{PROJECT_NAME}}
{messages}{dependencies})

ament_export_dependencies(rosidl_default_runtime)
ament_package()
"""


def messages_of(package, definitions=None):
    """`{ShortName: msg text}` for one package, out of the definitions the writer uses."""
    definitions = ros_schema.EXTRA_DEFINITIONS if definitions is None else definitions
    prefix = f"{package}/msg/"
    return {
        name[len(prefix) :]: text for name, text in definitions.items() if name.startswith(prefix)
    }


def dependencies_of(messages, package):
    """Every other package these messages name, read off the field types.

    A field type is `pkg/Type` or a bare builtin, so anything with a slash is a dependency.
    `std_msgs` arrives this way through `std_msgs/Header`, and `geometry_msgs` through
    `geometry_msgs/Point` - neither is hardcoded, so a definition that grows a field cannot leave
    the package.xml behind.
    """
    found = set()
    for text in messages.values():
        for line in text.strip().splitlines():
            field_type = line.split()[0]
            # Arrays: `wingfin_msgs/TrafficLight[] lights` names its own package, which is not a
            # dependency of itself and must not be declared as one - ament fails on the cycle.
            head = field_type.split("[")[0]
            if "/" in head and head.split("/")[0] != package:
                found.add(head.split("/")[0])
    return sorted(found)


def package_files(package, definitions=None):
    """The whole package as `{relative path: contents}`, ready to write or to assert on."""
    messages = messages_of(package, definitions)
    if not messages:
        raise ValueError(f"no {package} messages in the definitions")
    depends = dependencies_of(messages, package)

    files = {f"msg/{name}.msg": text for name, text in sorted(messages.items())}
    files["package.xml"] = PACKAGE_XML.format(
        package=package,
        description=f"Message types {package} defines, generated from ros_schema.py",
        depends="".join(f"  <depend>{name}</depend>\n" for name in depends),
    )
    files["CMakeLists.txt"] = CMAKELISTS.format(
        package=package,
        finds="".join(f"find_package({name} REQUIRED)\n" for name in depends),
        messages="".join(f'  "msg/{name}.msg"\n' for name in sorted(messages)),
        dependencies=f"  DEPENDENCIES {' '.join(depends)}\n" if depends else "",
    )
    return files


def write(package, destination, definitions=None):
    destination = Path(destination)
    for relative, contents in package_files(package, definitions).items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", help="e.g. wingfin_msgs")
    parser.add_argument("destination", help="directory to write the package into")
    arguments = parser.parse_args(argv)
    try:
        written = write(arguments.package, arguments.destination)
    except ValueError as error:
        print(f"\n  {error}\n", file=sys.stderr)
        return 1
    print(f"{arguments.package} -> {written}")
    for relative in sorted(package_files(arguments.package)):
        print(f"  {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
