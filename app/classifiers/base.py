from dataclasses import dataclass
from typing import Protocol, Dict, Any

from ..categories import CategoryPath
from ..folder_action import FolderAction, FolderActionRequest

# Re-export for convenience
__all__ = ["Classifier", "ClassifierResponse", "FolderActionResponse"]


@dataclass
class ClassifierResponse:
    """Response from a classifier containing category path, metrics, and error info."""

    path: CategoryPath
    metrics: Dict[str, Any]
    error: Exception | None = None
    error_context: Dict[str, Any] | None = None

    def __str__(self) -> str:
        if self.error:
            return f"Error: {self.error}"
        return str(self.path)

    def __repr__(self) -> str:
        parts = [f"path={self.path!r}", f"metrics={self.metrics!r}"]
        if self.error:
            parts.append(f"error={self.error!r}")
        if self.error_context:
            parts.append(f"error_context={self.error_context!r}")
        return f"ClassifierResponse({', '.join(parts)})"

    @property
    def failed(self) -> bool:
        """Returns True if classification failed with an error."""
        return self.error is not None

    def metadata(self) -> Dict[str, Any]:
        payload = self.metrics.get("metadata") if self.metrics else None
        if isinstance(payload, dict):
            return dict(payload)
        return {}


@dataclass
class FolderActionResponse:
    """Response from classifier about folder action.
    
    Classifiers can either make a DECISION (definitive answer) or provide a HINT
    (suggestion for next classifier in chain).
    """
    action: FolderAction | None
    is_final: bool  # True = decision, False = hint (delegate to next classifier)
    hint: FolderAction | None = None  # Suggestion for next classifier
    reason: str | None = None  # Why this decision/hint was made
    
    @classmethod
    def decision(cls, action: FolderAction, reason: str | None = None) -> "FolderActionResponse":
        """Create a final decision response."""
        return cls(action=action, is_final=True, hint=None, reason=reason)
    
    @classmethod
    def delegate(cls, hint: FolderAction | None = None, reason: str | None = None) -> "FolderActionResponse":
        """Delegate to next classifier with optional hint."""
        return cls(action=None, is_final=False, hint=hint, reason=reason)
    
    def __str__(self) -> str:
        if self.is_final:
            return f"decision:{self.action}"
        if self.hint:
            return f"delegate:hint={self.hint}"
        return "delegate:no_hint"


class Classifier(Protocol):
    """Protocol defining the interface for file classifiers."""

    async def classify(
        self,
        name: str,
        rel_path: str,
        mime: str,
        sample: str,
        hint: dict | None = None,
    ) -> ClassifierResponse:
        ...

    async def close(self):
        ...

    def advise_folder_action(self, request: FolderActionRequest) -> FolderActionResponse:
        """Advise on folder action.
        
        Returns:
            FolderActionResponse with either:
            - decision (is_final=True): classifier made final decision
            - delegation (is_final=False): pass to next classifier, optionally with hint
        """
        ...

    def ensure_available(self) -> bool:
        ...

    def display_name(self) -> str:
        ...

    def is_ai(self) -> bool:
        ...
