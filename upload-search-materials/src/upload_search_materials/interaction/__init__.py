"""Declarative contracts for the local interaction workflow."""

from .stages import FieldDefinition, STAGES, StageDefinition, get_stage
from .session import InteractionConflict, InteractionPathError, SessionRecord, SessionStore

__all__ = [
    "FieldDefinition",
    "InteractionConflict",
    "InteractionPathError",
    "SessionRecord",
    "SessionStore",
    "STAGES",
    "StageDefinition",
    "get_stage",
]
