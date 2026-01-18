"""Action types for organizing file trees."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FolderAction(str, Enum):
    """Actions that can be taken on folders during file organization.
    
    KEEP: Preserve the folder structure as-is (move as a unit)
    KEEP_PARENT: Signal that the parent folder should be kept together (structural marker)
    KEEP_EXCEPT: Keep folder but allow deeper paths to override with disaggregate
    DISAGGREGATE: Break apart the folder, organize files individually
    """
    KEEP = "keep"
    KEEP_PARENT = "keep_parent"
    KEEP_EXCEPT = "keep_except"
    DISAGGREGATE = "disaggregate"
    
    def __str__(self) -> str:
        return self.value
    
    @classmethod
    def from_string(cls, value: str | None) -> "FolderAction":
        """Convert string to FolderAction.
        
        Raises:
            ValueError: If value is not a recognized action
        """
        if not value:
            raise ValueError("FolderAction cannot be empty")
        
        normalized = value.strip().lower()
        
        # Handle common variations and legacy names
        if normalized in ("move_as_unit", "moveasunit", "unit"):
            return cls.KEEP
        if normalized in ("strip", "disaggregate"):
            # STRIP and DISAGGREGATE are the same - both mean break apart
            return cls.DISAGGREGATE
        if normalized in ("keep_parent", "keepparent", "parent"):
            return cls.KEEP_PARENT
        if normalized in ("keep_except", "keepexcept"):
            return cls.KEEP_EXCEPT
        
        for action in cls:
            if action.value == normalized:
                return action
        
        raise ValueError(f"Unknown FolderAction: {value!r}. Valid: keep, keep_parent, keep_except, disaggregate")


class RequiresAI(str, Enum):
    """Whether a rule requires AI consultation.
    
    FINAL: Rule is final, don't consult AI
    AI: Consult AI classifier for decision
    """
    FINAL = "final"
    AI = "ai"
    
    def __str__(self) -> str:
        return self.value
    
    # TODO: this needs to have an optional default value
    # so when we parse AI answer, and it does not match anything - we use the default value and LOG(WARINNG)
    # in other case (parsing rules.csv) - we raise ValueError
    @classmethod
    def from_string(cls, value: str | None) -> "RequiresAI":
        """Convert string to RequiresAI.
        
        Raises:
            ValueError: If value is not recognized
        """
        if not value:
            raise ValueError("RequiresAI cannot be empty")
        
        normalized = value.strip().lower()
        
        for req in cls:
            if req.value == normalized:
                return req
        
        raise ValueError(f"Unknown RequiresAI: {value!r}. Valid: final, ai")


@dataclass
class FolderActionRequest:
    """Request to determine folder action with standardized structure."""
    folder_path: str
    folder_name: str
    children: list[dict]
    total_files: int
    rule_hint: FolderAction | None = None
    
    @classmethod
    def from_payload(cls, payload: dict) -> FolderActionRequest:
        """Create from legacy dict payload for backward compatibility."""
        folder_path = payload.get("folder_path", payload.get("folder", ""))
        folder_name = payload.get("folder_name", folder_path.rstrip('/').split('/')[-1] if folder_path else "")
        children = payload.get("children", [])
        total_files = payload.get("total_files", 0)
        
        rule_hint_raw = payload.get("rule_hint")
        rule_hint = None
        if rule_hint_raw:
            try:
                rule_hint = FolderAction.from_string(str(rule_hint_raw))
            except ValueError:
                pass
        
        return cls(
            folder_path=folder_path,
            folder_name=folder_name,
            children=children,
            total_files=total_files,
            rule_hint=rule_hint,
        )
