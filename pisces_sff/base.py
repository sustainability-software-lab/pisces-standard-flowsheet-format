from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SFFExporter(Protocol):
    """Contract for simulator → SFF export adapters.

    Each simulator subpackage (e.g. pisces_sff.biosteam) should provide an
    object that satisfies this protocol.  The adapter is responsible only for
    the data conversion; file I/O is handled separately by
    :func:`pisces_sff.core.io.write_sff`.

    Example usage::

        from pisces_sff.core.io import write_sff

        sff_dict = adapter.export(system)
        write_sff(sff_dict, "output.json")
    """

    def export(self, system: object) -> dict:
        """Convert a simulated system to an SFF document dict.

        Args:
            system: A simulator-native system or flowsheet object.

        Returns:
            An SFF document as a Python dict suitable for passing to
            :func:`pisces_sff.core.io.write_sff` or
            :func:`pisces_sff.core.validate.validate_sff`.
        """
        ...


@runtime_checkable
class SFFImporter(Protocol):
    """Contract for SFF → simulator import adapters.

    Defined now so that future import implementations across different
    simulator subpackages use a consistent interface.  The adapter receives a
    pre-parsed dict (from :func:`pisces_sff.core.io.read_sff`) and is
    responsible only for the data conversion back to a simulator-native object.

    Example usage::

        from pisces_sff.core.io import read_sff

        sff_dict = read_sff("flowsheet.json")
        native_system = adapter.import_sff(sff_dict)
    """

    def import_sff(self, data: dict) -> object:
        """Convert an SFF document dict to a simulator-native object.

        Args:
            data: An SFF document as a Python dict, e.g. from
                :func:`pisces_sff.core.io.read_sff`.

        Returns:
            A simulator-native representation of the flowsheet.
        """
        ...
