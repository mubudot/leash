"""Reuse the unmodified integration harness with deterministic block timestamps."""
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile

from build import artifact

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'e2e'))
import test_vault as h

original_chain = h.local_chain


@contextmanager
def deterministic_chain(chain_id=31337, *, steps_tracing=False):
    with original_chain(chain_id, steps_tracing=steps_tracing):
        h.rpc('anvil_setBlockTimestampInterval', 1)
        assert h.now() == 1793664000
        yield


h.local_chain = deterministic_chain


@contextmanager
def compiled_vault(variant):
    """Select an artifact without copying or rewriting any integration tests."""
    original_root = h.ROOT
    try:
        with tempfile.TemporaryDirectory(prefix='leash-artifact-') as temp:
            project = Path(temp)
            (project / 'out').symlink_to(artifact(variant).parent, target_is_directory=True)
            h.ROOT = project
            yield
    finally:
        h.ROOT = original_root
