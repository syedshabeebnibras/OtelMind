"""Abstract base class for pluggable remediation strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RemediationStrategy(ABC):
    """Base class that all remediation strategies must implement.

    Subclasses provide a single ``execute`` method that receives the failure
    classification and surrounding context, then returns a result dict
    describing what action was taken and whether it succeeded.
    """

    @abstractmethod
    async def execute(
        self,
        classification: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the remediation action.

        Parameters
        ----------
        classification:
            Failure classification details including ``failure_type``,
            ``confidence``, ``evidence``, and ``trace_id``.
        context:
            Additional runtime context such as span data, graph state,
            or configuration overrides.

        Returns
        -------
        A result dict containing at minimum a ``status`` key
        (e.g. ``"success"``, ``"failed"``, ``"skipped"``).
        """
