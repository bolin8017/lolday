"""L-clone-bandwidth: vcjob initContainer clone command includes --filter=blob:limit=10m."""

import inspect
import re


def test_build_clone_command_has_blob_limit_filter():
    """The clone-init args string must include --filter=blob:limit=10m."""
    from app.services import build

    src = inspect.getsource(build)
    assert "clone --depth=1" in src, "smoke check: clone literal still present"
    m = re.search(r'clone\s+--depth=1[^"\']*', src)
    assert m is not None
    assert "--filter=blob:limit=10m" in m.group(0), (
        "expected --filter=blob:limit=10m in the clone args; got: " + m.group(0)
    )
