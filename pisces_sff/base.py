from __future__ import annotations

from pathlib import Path
from typing import Protocol, Union, runtime_checkable


@runtime_checkable
class SFFExporter(Protocol):
    """Contract for simulator → SFF export adapters.

    Each simulator subpackage (e.g. pisces_sff.biosteam) should provide a
    callable that satisfies this protocol.
    """

    def export(self, system: object, file_path: Union[str, Path]) -> None:
        """Export a simulated system to an SFF JSON file.

        Args:
            system: A simulator-native system or flowsheet object.
            file_path: Destination path for the output JSON file.
        """
        ...


@runtime_checkable
class SFFImporter(Protocol):
    """Contract for SFF → simulator import adapters.

    Defined now so that future load/import implementations across different
    simulator subpackages use a consistent interface.
    """

    def load(self, file_path: Union[str, Path]) -> object:
        """Load an SFF JSON file and return a simulator-native object.

        Args:
            file_path: Path to an SFF JSON file.

        Returns:
            A simulator-native representation of the flowsheet.
        """
        ...
