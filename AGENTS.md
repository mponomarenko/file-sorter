# AI Documentation

To test run ./test.sh
Public full test: ./full_test.sh (uses repo fixtures)
Private full test: ./tests/private/full_test_private.sh (requires private paths)
Docker-based tests may take a long time to build images and download deps.

The application is intended to run as docker container. Update requirements.txt and Dockerfile to add new ones. It will not run in standard environment.

do not add any optional dependencies - install them in the .venv and add to requirements.txt and into docker. never try/catch around imports. Never import inside functions. All imports need to be top level.

try to avoid getattr checks - we know the context and code path, we either always have the right attributes/methods in classes in our codebase, or we dont.  This still may be appropriate when dealing with external data

When accessing configuration, use direct attributes (e.g. `cfg.FOO`) and ensure every such attribute has a default defined on `AppConfig`.

Do not comment on WHAT we do, add comments WHY we do something.

All rules and file name matching of any kind have to be in rules.csv and not in python code

Assume all paths are unix "/" - never "\". There is no need for path utils - we can always assume all paths are posix

Avoid functions or tuples with more than 4 items - make @dataclass to hold the values

Avoid having multiple disjoint logical pieces in the same file and then having to do extra work to avoid import loops - keep each logical piece of code in its own file to make import loops impossible.

Never hardcode any path matching, string prefix matching etc for the incoming path. All such logic needs to be in rules text. Rules can have named regexp groups that directly propagate into metadata. If absolutely have to add special case logic - use said metadata as trigger.

High level design is in File-Sorter.md - most importantly - no hardcoded path or mime matching - everything has to be handled as rules and in generic way.

Avoid having optional arguments or arguments of multiple types foo(a: b|c|None) - if we are migrating from b to c - just change ALL code to use c and if foo actually needs c or would throw - just have foo(a:c) at the end.

avoid if type checks: instanceoff, hasattr, is a, etc for arguments: use typed argument of as specific type as possible.
