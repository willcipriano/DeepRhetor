"""LangGraph checkpointer backed by the project SQLite ``checkpoint`` table.

Domain tables remain authoritative. LangGraph opaque state is stored via
:class:`~deeprhetor.services.checkpoint.CheckpointStore` under the ``lg:`` prefix
so interrupts and resume survive process restart.
"""

from __future__ import annotations

import base64
import pickle
from collections import defaultdict
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
)
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.services.checkpoint import CheckpointStore

NODE_NAME = "langgraph_saver"
NAMESPACE = "workflow"


class ProjectSqliteSaver(BaseCheckpointSaver):
    """In-memory LangGraph saver mirrored to ``CheckpointStore`` after each write."""

    def __init__(self, engine: AsyncEngine, *, run_id: str) -> None:
        super().__init__()
        self._inner = InMemorySaver()
        self._store = CheckpointStore(engine)
        self._run_id = run_id
        self._hydrated = False

    @property
    def memory(self) -> InMemorySaver:
        return self._inner

    def _snapshot_blob(self) -> str:
        # Convert defaultdict factories to plain dicts so pickle is reliable.
        storage = {
            thread_id: {ns: dict(checkpoints) for ns, checkpoints in ns_map.items()}
            for thread_id, ns_map in self._inner.storage.items()
        }
        payload = {
            "storage": storage,
            "writes": dict(self._inner.writes),
            "blobs": dict(self._inner.blobs),
        }
        return base64.b64encode(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)).decode(
            "ascii"
        )

    def _load_blob(self, blob: str) -> None:
        payload = pickle.loads(base64.b64decode(blob.encode("ascii")))
        storage: defaultdict[Any, defaultdict[Any, dict]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for thread_id, ns_map in payload["storage"].items():
            for ns, checkpoints in ns_map.items():
                storage[thread_id][ns].update(checkpoints)
        writes: defaultdict[Any, dict] = defaultdict(dict)
        writes.update(payload["writes"])
        blobs: defaultdict[Any, Any] = defaultdict()
        blobs.update(payload["blobs"])
        self._inner.storage = storage
        self._inner.writes = writes
        self._inner.blobs = blobs

    async def _ensure_hydrated(self) -> None:
        if self._hydrated:
            return
        latest = await self._store.latest(
            run_id=self._run_id, node_name=NODE_NAME, namespace=NAMESPACE
        )
        if latest is not None and "blob" in latest.payload:
            self._load_blob(latest.payload["blob"])
        self._hydrated = True

    async def _persist(self) -> None:
        await self._store.put(
            run_id=self._run_id,
            node_name=NODE_NAME,
            namespace=NAMESPACE,
            payload={"blob": self._snapshot_blob()},
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._inner.get_tuple(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        yield from self._inner.list(config, filter=filter, before=before, limit=limit)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self._inner.put(config, checkpoint, metadata, new_versions)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return self._inner.put_writes(config, writes, task_id, task_path=task_path)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        await self._ensure_hydrated()
        return self._inner.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        await self._ensure_hydrated()
        for item in self._inner.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        await self._ensure_hydrated()
        result = self._inner.put(config, checkpoint, metadata, new_versions)
        await self._persist()
        return result

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await self._ensure_hydrated()
        self._inner.put_writes(config, writes, task_id, task_path=task_path)
        await self._persist()


def thread_config(run_id: str, *, checkpoint_id: str | None = None) -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": run_id}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


__all__ = [
    "NAMESPACE",
    "NODE_NAME",
    "ProjectSqliteSaver",
    "get_checkpoint_id",
    "thread_config",
]
