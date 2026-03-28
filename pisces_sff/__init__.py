from .base import SFFExporter, SFFImporter
from .core.validate import validate_sff
from .core.io import read_sff, write_sff

__all__ = ["SFFExporter", "SFFImporter", "validate_sff", "read_sff", "write_sff",
           "export_biosteam_flowsheet_sff"]


def __getattr__(name: str):
    if name == "export_biosteam_flowsheet_sff":
        try:
            from .biosteam import export_biosteam_flowsheet_sff as _fn
            return _fn
        except ImportError as exc:
            raise ImportError(
                "export_biosteam_flowsheet_sff requires the 'biosteam' extra. "
                "Install it with: pip install pisces-sff[biosteam]"
            ) from exc
    raise AttributeError(f"module 'pisces_sff' has no attribute {name!r}")
