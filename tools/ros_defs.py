"""Read every message definition **out of a bag**, in the form `EXTRA_DEFINITIONS` wants.

    uv run python tools/ros_defs.py bags/j1-lights
    uv run python tools/ros_defs.py /path/to/ros2_mig_phase_5_p1 --all
    uv run python tools/ros_defs.py /path/to/ros2_mig_phase_5_p1 \
        --write tools/wingfin_msgs --package wingfin_msgs

`ros_schema.MISSING_DEFINITIONS` lists 24 of the rig's topics we do not publish, each because we
have no `.msg` text for its type - or, for two of them, no knowledge of which standard type the
rig put on it. The reason used to read "type not in the audit", which was true and misleading:
**`bag_audit.html` records rates, not types** - grep every
message type named anywhere in that file and exactly one comes back, `geometry_msgs/TwistStamped`
- so the audit was never going to carry them. It was the wrong place to look.

**The bag itself carries them.** rosbag2 writes the full `.msg` text of every type it records
into the file, dependencies concatenated after `MSG:` separators, so that a reader can decode a
bag without the package that wrote it. That is not a nicety of the format, it is the reason
`ros_schema.py` felt free to invent `wingfin_msgs/TrafficLight` at all. Which means the rig's own
`ros2_mig_phase_5_p1` bag already contains the exact definition of every type we lack - not a
guess at one, the bytes the rig serialised against. Nothing has to be requested from whoever owns
the wingfin package, and the rig does not have to be running. One `.mcap` file is the whole
input.

This tool is what turns that from a fact into a command. It prints each recovered definition as a
`dict` entry that pastes verbatim into `ros_schema.EXTRA_DEFINITIONS`, and it **parses every one
before printing it**, because the failure this whole approach exists to avoid is a field in the
wrong order: that serialises silently and deserialises into nonsense, which is worse than an
absent topic and is exactly why these are copied rather than inferred.

`--write` skips the paste and puts each one in a `.msg` file beside the loader that reads it,
which is where the fifteen belong: **verbatim has to be checkable**, and a file diffs against the
bag it came out of in one command where the same text rewrapped into a Python literal does not.
`--package` keeps one directory to one package, the way `tools/sbg_msgs/` holds `sbg_driver` and
nothing else. `ros_schema._wingfin_definitions` loads `tools/wingfin_msgs/` at import, so what
this writes registers with no edit to any source file.

It also answers the question anyone pointing it at a rig bag is actually asking - *"is this file
enough?"* - by naming the fifteen blocked topics one by one with whatever type the recorder filed
each under. That lookup runs topic-first because **the fifteen type names are themselves
unknown**; that is the blockage, not a convenience.

By default only the unknown types are printed - the ones `Stores.ROS2_HUMBLE` has never heard of,
which is precisely the set worth pasting. `--all` prints the core types too, which is only useful
for confirming that a bag says what you think it says.

**Where this can come back empty, and it is not a bug here.** A definition is written by the
recorder, so a bag whose writer supplied none carries none; `MessageDefinition.format` is then
`NONE` and this prints the topic under "no definition recorded" rather than inventing one. That
is the honest answer, and it is the one thing worth checking first on a bag off the rig.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ros_audit  # noqa: E402
import ros_schema  # noqa: E402

#: The line rosbag2 puts between concatenated definitions. Matched by prefix, not by length: the
#: run of `=` is 80 characters from `rosbags` and has been other lengths from other writers, and
#: a definition that failed to split would silently arrive as one enormous unparseable blob.
SEPARATOR = "===="

#: What a dependency block announces itself with, e.g. `MSG: geometry_msgs/Point`.
HEADER = "MSG:"


@dataclass
class Definition:
    """One message type's `.msg` text, and where in the bag it was found."""

    name: str
    text: str
    topics: list[str] = field(default_factory=list)
    #: Set when two connections disagree about one type's text. Never expected, always reported:
    #: a bag holding two definitions of one name cannot be pasted anywhere without a choice.
    conflicts: list[str] = field(default_factory=list)


def normalise(name: str) -> str:
    """`geometry_msgs/Point` -> `geometry_msgs/msg/Point`, and leave a normalised name alone.

    Dependency headers inside a definition use the two-part ROS 1 spelling; connections and
    `EXTRA_DEFINITIONS` use the three-part ROS 2 one. Keying a dict on both spellings of one type
    is how a "missing" definition ends up sitting in the file under a name nothing looks up.
    """
    parts = name.strip().split("/")
    if len(parts) == 2:
        return f"{parts[0]}/msg/{parts[1]}"
    return name.strip()


def split(msgtype: str, text: str) -> dict[str, str]:
    """One connection's concatenated definition, as `{type name: its own .msg text}`.

    The first block is the connection's own type and carries no header; every later block is
    introduced by a separator line and a `MSG:` line naming it.
    """
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.startswith(SEPARATOR):
            blocks.append([])
            continue
        blocks[-1].append(line)

    out: dict[str, str] = {}
    for index, lines in enumerate(blocks):
        if index == 0:
            name = normalise(msgtype)
        elif lines and lines[0].startswith(HEADER):
            name = normalise(lines[0][len(HEADER) :])
            lines = lines[1:]
        else:
            # A separator with no `MSG:` after it. Not something any writer produces; skipped
            # loudly rather than guessed at, since the alternative is filing a body under the
            # previous block's name.
            continue
        body = "\n".join(lines).strip("\n")
        if body:
            out[name] = body + "\n"
    return out


def read(path) -> tuple[dict[str, Definition], list[tuple[str, str]]]:
    """Every definition in a bag, plus `(topic, type)` for connections that recorded none."""
    from rosbags.interfaces import MessageDefinitionFormat
    from rosbags.rosbag2 import Reader

    path = ros_audit.refuse_if_missing(path)

    found: dict[str, Definition] = {}
    undefined: list[tuple[str, str]] = []
    with Reader(path) as reader:
        for connection in reader.connections:
            definition = connection.msgdef
            # `msgdef` is a `MessageDefinition`, not a string, and has `.data` / `.format` - not
            # `.encoding`. Tolerating a bare string here costs one `getattr` and keeps this
            # working against the older `rosbags` the container may pin.
            body = getattr(definition, "data", definition) or ""
            fmt = getattr(definition, "format", MessageDefinitionFormat.MSG)
            if not body or fmt == MessageDefinitionFormat.NONE:
                undefined.append((connection.topic, connection.msgtype))
                continue
            if fmt == MessageDefinitionFormat.IDL:
                # Kept whole. IDL does not split on `MSG:` and does not paste into
                # `EXTRA_DEFINITIONS`, which is `.msg` text; saying so beats emitting something
                # that looks pasteable and is not.
                pieces = {normalise(connection.msgtype): body}
            else:
                pieces = split(connection.msgtype, body)
            for name, text in pieces.items():
                entry = found.setdefault(name, Definition(name=name, text=text))
                if entry.text != text and text not in entry.conflicts:
                    entry.conflicts.append(text)
                entry.topics.append(connection.topic)
    return found, undefined


def known(name: str) -> bool:
    """Does the stock humble typestore already carry this type?"""
    from rosbags.typesys import Stores, get_typestore

    return name in get_typestore(Stores.ROS2_HUMBLE).fielddefs


def parses(name: str, text: str) -> str | None:
    """`None` if the text parses as this type, else the parser's complaint.

    The gate the whole tool turns on. A definition that will not parse must never reach the
    clipboard, because the next thing that happens to it is a paste into a table whose entries
    are trusted without further checking.
    """
    from rosbags.typesys import get_types_from_msg

    try:
        get_types_from_msg(text, name)
    except Exception as error:  # noqa: BLE001 - the parser raises several unrelated types
        return f"{type(error).__name__}: {error}"
    return None


#: The repo's ruff limit. Rendered output that overruns it cannot be pasted without a reformat,
#: and a definition reflowed by hand is a definition that can lose a line.
WIDTH = 100


def quote(text: str) -> list[str]:
    """`text` as one Python string literal per line group, each fitting inside `WIDTH`.

    Split on line boundaries only. A `.msg` field spans no more than one line, so a break inside
    one would be gratuitous, and the escaped form stays diff-able against the source definition.
    """
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        escaped = (current + line).encode("unicode_escape").decode()
        # 8 for the continuation indent, 2 for the quotes.
        if current and len(escaped) + 10 > WIDTH:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return [f'        "{chunk.encode("unicode_escape").decode()}"' for chunk in chunks]


def vendor(entry: Definition, directory: Path, force=False) -> tuple[str, str]:
    """Write one recovered definition to `<directory>/<Type>.msg`. Returns `(verdict, detail)`.

    The alternative to this is a paste into a Python string literal, and `tools/sbg_msgs/`
    already argues why that is the worse of the two: **verbatim has to be checkable.** A `.msg`
    file can be diffed against the bag it came out of, or against whatever the package's owner
    eventually publishes, in one command; the same text rewrapped to fit a source line cannot,
    and rewrapping is exactly where a field changes order.

    The one thing this refuses to do quietly is **overwrite a definition that disagrees with the
    one already on disk.** That is not a hypothetical here. `wingfin_msgs/TrafficLightArray` is a
    type *we* invented for a topic the rig does not have, and it sits in the same package
    namespace as the rig's own types - so a rig bag that turns out to carry a real
    `wingfin_msgs/TrafficLightArray` would land on top of ours, and every bag written afterwards
    would serialise our traffic lights against a field list nothing in this repo agreed to. A
    collision is a question for a person, so it is reported and skipped rather than resolved.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{entry.name.rsplit('/', 1)[-1]}.msg"
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if existing == entry.text:
            return "unchanged", str(target)
        if not force:
            return "CONFLICT", (
                f"{target} already holds a different definition of {entry.name} - "
                "diff the two and decide, or re-run with --force"
            )
    target.write_text(entry.text, encoding="utf-8")
    return "written", str(target)


def blocked_report(found: dict[str, Definition], out) -> None:
    """What this bag does for the topics phase 5 is waiting on, named one by one.

    The question anyone pointing this tool at a rig bag is actually asking is *"is this file
    enough?"*, and the count of definitions above does not answer it. **We do not know the type
    names of the fifteen** - that is the whole blockage, since `bag_audit.html` records rates and
    not types - so the lookup has to run the other way round, from the topic to whatever type the
    recorder filed it under. A bag that carries fourteen of the fifteen is a bag worth having and
    is not the end of the job, and only a per-topic list says which one is short.
    """
    waiting = sorted(ros_schema.MISSING_DEFINITIONS)
    if not waiting:
        return
    by_topic: dict[str, str] = {}
    for entry in found.values():
        for topic in entry.topics:
            by_topic.setdefault(topic, entry.name)

    covered = [topic for topic in waiting if topic in by_topic]
    print(f"\n  the {len(waiting)} topics phase 5 waits on: {len(covered)} carried by this bag",
          file=out)
    for topic in waiting:
        name = by_topic.get(topic)
        print(f"    {'+' if name else '-'} {topic}  {name or 'not in this bag'}", file=out)
    if not covered:
        print(
            "  None of them. This is a bag of ours, not one off the rig - the definitions the\n"
            "  fifteen need live in the rig's own ros2_mig_phase_5_p1 bag and nowhere else.",
            file=out,
        )


def render(entry: Definition) -> str:
    """One `EXTRA_DEFINITIONS` entry, ready to paste."""
    body = quote(entry.text)
    # The one-liner only when the *whole* line fits - the key is part of the width, and a long
    # type name is exactly where that stops being true.
    single = f'    "{entry.name}": {body[0].strip()},'
    if len(body) == 1 and len(single) <= WIDTH:
        return single
    joined = "\n".join(body)
    return f'    "{entry.name}": (\n{joined}\n    ),'


def report(path, show_all=False, out=None, write=None, force=False, package=None) -> bool:
    """Print every definition worth pasting. `False` if anything could not be parsed.

    `write` vendors the recovered definitions as `.msg` files under that directory instead of
    only printing them, which is what makes phase 5 a command rather than a paste. A conflict
    there is a failure: it means two different field lists are in play under one type name.
    """
    # Resolved here, not in the signature: a default argument is bound at import, which pins
    # this to whatever `sys.stdout` was then and makes the output invisible to any caller that
    # redirects it - `capsys` in the tests being the first one to notice.
    out = sys.stdout if out is None else out
    found, undefined = read(path)
    have = set(ros_schema.EXTRA_DEFINITIONS)

    unknown = sorted(name for name in found if not known(name))
    printable = sorted(found) if show_all else unknown
    new = [name for name in printable if name not in have]

    print(f"{path}: {len(found)} message definitions across the bag's connections", file=out)
    print(
        f"  {len(unknown)} outside the humble typestore, "
        f"{len([n for n in unknown if n in have])} of which ros_schema already carries",
        file=out,
    )

    ok = True
    for name in printable:
        entry = found[name]
        complaint = parses(name, entry.text)
        if complaint:
            ok = False
            print(f"\n  ! {name} does not parse - {complaint}", file=out)
            print(f"    seen on {', '.join(sorted(set(entry.topics)))}", file=out)
        if entry.conflicts:
            ok = False
            count = len(entry.conflicts) + 1
            print(f"\n  ! {name} has {count} different definitions in one bag", file=out)

    if write is not None:
        directory = Path(write)
        chosen = [n for n in printable if package is None or n.split("/")[0] == package]
        print(f"\n  vendoring {len(chosen)} definition(s) into {directory}/", file=out)
        for name in chosen:
            verdict, detail = vendor(found[name], directory, force=force)
            if verdict == "CONFLICT":
                ok = False
            print(f"    {verdict:>9}  {name}\n               {detail}", file=out)
    elif new:
        print(f"\n# paste into ros_schema.EXTRA_DEFINITIONS - {len(new)} new:", file=out)
        for name in new:
            print(render(found[name]), file=out)
    elif printable:
        print("\n  nothing new - every definition here is already known", file=out)

    blocked_report(found, out)

    if undefined:
        print(f"\n  no definition recorded for {len(undefined)} connection(s):", file=out)
        for topic, msgtype in sorted(undefined):
            print(f"    {topic}  ({msgtype})", file=out)
        print(
            "  The recorder wrote none. Nothing can be recovered from this bag for those types;\n"
            "  a bag from a recorder that supplies definitions is the only way to get them.",
            file=out,
        )
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bag")
    parser.add_argument(
        "--all",
        action="store_true",
        help="print the core types too, not only the ones the humble typestore lacks",
    )
    parser.add_argument(
        "--write",
        metavar="DIR",
        help="write each definition to DIR/<Type>.msg instead of printing it, the way "
        "tools/sbg_msgs/ holds the SBG family",
    )
    parser.add_argument(
        "--package",
        help="with --write, vendor only this package's types, e.g. wingfin_msgs. One directory "
        "holds one package, the way tools/sbg_msgs/ holds sbg_driver",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --write, overwrite a file that holds a different definition of the same type",
    )
    arguments = parser.parse_args(argv)
    # A refusal, not a traceback -- see the same guard in `ros_audit.main` and `ros_probe.main`.
    try:
        return (
            0
            if report(
                arguments.bag,
                arguments.all,
                write=arguments.write,
                force=arguments.force,
                package=arguments.package,
            )
            else 1
        )
    except ValueError as error:
        print(f"\n  {error}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
