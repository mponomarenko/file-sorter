from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable, Tuple

from .categories import CategoryPath


@dataclass(frozen=True)
class FullPath:
    original: PurePosixPath
    source_prefix: Tuple[str, ...] = field(default_factory=tuple)
    disaggregated: Tuple[str, ...] = field(default_factory=tuple)
    kept: Tuple[str, ...] = field(default_factory=tuple)
    kept_role: str = "suffix"
    file: str = ""

    def parts(self) -> Tuple[str, ...]:
        segments = list(self.source_prefix) + list(self.disaggregated) + list(self.kept)
        if self.file:
            segments.append(self.file)
        return tuple(segments)

    def render(self) -> str:
        segments: list[str] = []
        if self.source_prefix:
            segments.append(f"[source/{'/'.join(self.source_prefix)}]")
        if self.disaggregated:
            segments.append(f"[disagg/{'/'.join(self.disaggregated)}]")
        if self.kept:
            role = self.kept_role or "keep"
            segments.append(f"[{role}/{'/'.join(self.kept)}]")
        segments.append(self.file)
        return "/".join(seg for seg in segments if seg)


@dataclass(frozen=True)
class PathLayer:
    role: str
    parts: Tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        if not self.parts:
            return ""
        joined = "/".join(self.parts)
        return f"[{self.role}/{joined}]"


@dataclass
class ClassifiedPath:
    full_path: FullPath
    destination_path: PurePosixPath
    category_path: CategoryPath
    metadata: dict = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        destination: str,
        category: CategoryPath,
        metadata: dict | None = None,
        full_path: FullPath | None = None,
    ) -> "ClassifiedPath":
        if full_path is None:
            raise ValueError("FullPath is required for ClassifiedPath")
        return cls(
            destination_path=PurePosixPath(destination),
            category_path=category,
            metadata=dict(metadata or {}),
            full_path=full_path,
        )

    @property
    def source(self) -> str:
        return self.full_path.original.as_posix()

    @property
    def destination(self) -> str:
        return self.destination_path.as_posix()

    @property
    def layers(self) -> Tuple[PathLayer, ...]:
        layers: list[PathLayer] = []
        if self.full_path.source_prefix:
            layers.append(PathLayer("source", self.full_path.source_prefix))
        if self.full_path.disaggregated:
            layers.append(PathLayer("disagg", self.full_path.disaggregated))
        if self.category_path.parts:
            layers.append(PathLayer("category", self.category_path.parts))
        if self.full_path.kept:
            role = "keep" if self.full_path.kept_role == "keep" else "suffix"
            layers.append(PathLayer(role, self.full_path.kept))
        return tuple(layers)

    def explanation(self) -> str:
        rendered = self.full_path.render()
        return f"{rendered} -> {self.destination_path.name}"
