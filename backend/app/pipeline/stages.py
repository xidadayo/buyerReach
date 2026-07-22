from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class Stage(Protocol):
    name: str
    version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    retry_policy: dict[str, Any]
    error_mapping: dict[str, str]
    metrics: tuple[str, ...]

    def can_run(self, context: dict[str, Any]) -> bool: ...
    def execute(self, context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FunctionStage:
    name: str
    version: str
    executor: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    retry_policy: dict[str, Any] = field(
        default_factory=lambda: {"max_attempts": 3, "backoff_seconds": [5, 30, 120]}
    )
    error_mapping: dict[str, str] = field(
        default_factory=lambda: {"timeout": "retryable", "invalid_input": "terminal"}
    )
    metrics: tuple[str, ...] = ("queue_duration_ms", "duration_ms", "success", "cost")

    def can_run(self, context: dict[str, Any]) -> bool:
        return not context.get("cancelled") and context.get("budget_remaining", 1) >= 0

    def execute(self, context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return self.executor(context, payload)


class StageRegistry:
    def __init__(self) -> None:
        self._stages: dict[str, Stage] = {}

    def register(self, stage: Stage) -> None:
        key = f"{stage.name}:{stage.version}"
        if key in self._stages:
            raise ValueError(f"Stage already registered: {key}")
        self._stages[key] = stage

    def resolve(self, name: str, version: str) -> Stage:
        try:
            return self._stages[f"{name}:{version}"]
        except KeyError as exc:
            raise ValueError(f"Unknown stage: {name}:{version}") from exc

    def assemble(self, stage_versions: dict[str, str]) -> list[Stage]:
        return [self.resolve(name, version) for name, version in stage_versions.items()]
