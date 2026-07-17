"""State machine definitions for myERP domain entities."""
from .outsource_quote import OutsourceQuoteStateMachine
from .part import PartStateMachine

__all__ = [
    "OutsourceQuoteStateMachine",
    "PartStateMachine",
]
