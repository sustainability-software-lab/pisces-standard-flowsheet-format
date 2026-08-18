# API Reference

The public API is aggregated on the `pisces_sff` package. During the docs build
the heavy simulator (`biosteam`/`thermosteam`) is mocked, so these render from
signatures and docstrings without a simulation.

```{eval-rst}
.. currentmodule:: pisces_sff

.. autosummary::
   :toctree: generated
   :nosignatures:

   export_biosteam_flowsheet
   available_sff_versions
   get_purchase_cost_correlations
   validate_json_against_schema
   validate_flowsheet_against_SFF
   evaluate_sff_tags
   verify_reproducible
   CheckResult
   read_schema_version
```

## Exceptions

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   SFFError
   SFFExportError
   FlowsheetWriteError
   DesignInputSpecError
```
