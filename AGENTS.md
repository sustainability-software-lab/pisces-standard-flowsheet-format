# AGENTS.md

## Project Overview

This repository has two components:

1. **Standard Flowsheet Format (SFF)** — A JSON-based standard (JSON Schema Draft 7) for serializing chemical process flowsheets. Currently supports export from BioSTEAM. The schema captures unit operations, material/energy streams, thermodynamic properties, utility data, and cost estimates.

2. **Graph Learning** — A GNN-based experimentation framework (`graph_learning/`) that treats flowsheets as directed graphs and uses Graph Attention Networks (GATs) built on PyTorch Geometric to predict process-level properties (e.g., total installed cost).

**Core mental model**: A chemical process flowsheet is a directed graph where **unit operations are nodes** and **material/energy streams are edges**. The SFF schema serializes this structure; the graph learning pipeline converts it into PyTorch Geometric `Data` objects for GNN training and inference.

## Repository Structure

```
sff-graph-learning-sandbox/
├── export.py                           # BioSTEAM -> SFF export function
├── examples_for_export.py              # Usage examples for export.py
├── schema/
│   ├── schema_v_0.0.1.json             # Initial SFF schema
│   └── schema_v_0.0.2.json             # Current SFF schema
├── exported_flowsheets/
│   └── bioindustrial_park/             # 11 example SFF JSON files
├── graph_learning/
│   ├── config.yaml                     # Pipeline configuration (YAML)
│   ├── requirements.txt                # Python dependencies
│   ├── quick_demo.py                   # Minimal end-to-end demo
│   ├── main_pipeline.py                # Full configurable training pipeline
│   ├── demo_graph_generation.py        # Graph generation demo
│   ├── example_workflow.ipynb          # Interactive tutorial notebook
│   ├── graph_generation_deep_dive.ipynb
│   ├── src/
│   │   ├── data/                       # Data loading, feature extraction, graph building
│   │   ├── models/                     # GNN architectures
│   │   ├── training/                   # Training loops, early stopping, utilities
│   │   ├── evaluation/                 # Metrics and weighted scoring
│   │   ├── inference/                  # Prediction and LLM explanations
│   │   ├── agent/                      # Flowsheet design agent
│   │   └── deployment/                 # Production utilities
│   └── docs/                           # Internal design/implementation docs
├── docs/                               # MkDocs documentation source
├── mkdocs.yml                          # Documentation site config
└── LICENSE                             # MIT
```

## Data Pipeline

The graph learning pipeline follows a linear flow:

```
SFF JSON files
    -> FlowsheetDataLoader    (load and parse JSON)
    -> FeatureExtractor        (fit scalers/encoders, extract features)
    -> FlowsheetGraphBuilder   (convert to PyG Data objects)
    -> ProcessGNN              (train/predict)
```

### Key classes

| Class | File | Role |
|-------|------|------|
| `FlowsheetDataLoader` | `graph_learning/src/data/data_loader.py` | Loads SFF JSON files from a directory |
| `FeatureExtractor` | `graph_learning/src/data/feature_extractor.py` | Fits scalers on training data, extracts/normalizes node and edge features |
| `FlowsheetGraphBuilder` | `graph_learning/src/data/graph_builder.py` | Converts flowsheets to `torch_geometric.data.Data` objects |
| `ProcessGNN` | `graph_learning/src/models/process_gnn.py` | GAT/GATv2-based model for graph-level regression |
| `EnsembleProcessGNN` | `graph_learning/src/models/process_gnn.py` | Ensemble wrapper for uncertainty estimation |
| `Trainer` | `graph_learning/src/training/trainer.py` | Training loop with validation, early stopping, checkpointing |
| `WeightedScorer` | `graph_learning/src/evaluation/weighted_scorer.py` | Importance-weighted evaluation metrics |
| `Predictor` | `graph_learning/src/inference/predictor.py` | Inference with attention weight extraction |

### Feature mapping (SFF -> graph features)

**Node features** (from `units[]`):
- `unit_type_id` — encoded unit type (e.g., Distillation, HeatExchanger) via a `LabelEncoder` on `unit_type`
- Numerical: `installed_cost`, `purchase_cost`, `power_consumption`, `|heat_duty|`, `flow_rate` (from `design_results['Flow rate']`)
- Extracted in `FeatureExtractor._get_unit_features()`

**Edge features** (from `streams[]`):
- `mass_flow`, `molar_flow`, `volumetric_flow`, `temperature`, `pressure`, `price`
- Extracted in `FeatureExtractor._get_stream_features()`

**Edge connectivity**: Built from `streams[].source_unit_id` and `streams[].sink_unit_id`, mapped to node indices via `FlowsheetGraphBuilder._build_edge_index()`.

## Models

All models are in `graph_learning/src/models/` and inherit from `torch.nn.Module`.

| Model | Purpose |
|-------|---------|
| `ProcessGNN` | GAT/GATv2 with multi-head attention, batch norm, configurable pooling (mean/max/add), MLP readout head for regression |
| `EnsembleProcessGNN` | Wraps multiple `ProcessGNN` instances; returns mean prediction + uncertainty |
| `GraphVAE` | Variational autoencoder for flowsheet generation |
| `LinkPredictionGNN` | Predicts edge probabilities between nodes |
| `NodeTypePredictor` | Classifies unit operation types |
| `FlowsheetGenerator` | Orchestrates generation models for end-to-end flowsheet synthesis |

### ProcessGNN forward signature

```python
def forward(self, data):
    """
    Args:
        data: PyTorch Geometric Data object with:
            - x: Node features [num_nodes, num_node_features]
            - edge_index: Edge connectivity [2, num_edges]
            - edge_attr: Edge features [num_edges, num_edge_features] (optional)
            - batch: Batch vector [num_nodes]
    Returns:
        Predictions [batch_size, output_dim]
    """
```

## Configuration

All hyperparameters and paths are in `graph_learning/config.yaml`. Never hardcode values that belong in config.

```yaml
data:
  flowsheet_dir: "../exported_flowsheets/bioindustrial_park"
  output_dir: "outputs"

model:
  type: "GAT"
  num_gat_layers: 2
  hidden_channels: 64
  heads: 4
  dropout: 0.2
  output_dim: 1

training:
  batch_size: 8
  learning_rate: 0.001
  num_epochs: 200
  early_stopping_patience: 20

features:
  # NOTE: node_features/edge_features are currently illustrative only; the
  #       FeatureExtractor uses a fixed set of features and does not yet read
  #       these lists from config.
  node_features: [equipment_type_id, temperature, pressure, installed_cost]
  edge_features: [mass_flow, molar_flow, volumetric_flow, temperature, pressure, stream_price]
  target: "total_installed_cost"

scoring:
  weight_by: "installed_cost"

gemini:
  project_id: ""        # Set via environment variable
  location: "us-central1"
  model_name: "gemini-1.5-pro"
```

Config is loaded via `yaml.safe_load()` in pipeline scripts:

```python
with open(config_path, 'r') as f:
    config = yaml.safe_load(f)
```

## SFF Schema Reference

Current version: **v0.0.2** (`schema/schema_v_0.0.2.json`), JSON Schema Draft 7.

Top-level structure:
- `metadata` — source simulator, SFF version, process context (product name, organism, feedstock)
- `chemicals` — definition of chemical components and their properties
- `units[]` — array of unit operations. Each has: `id`, `unit_type`, `design_input_specs`, `thermo_property_package`, `reactions`, `design_results`, `installed_costs`, `purchase_costs`, `utility_consumption_results`, `utility_production_results`
- `streams[]` — array of connections. Each has: `id`, `source_unit_id`, `sink_unit_id`, `price`, `stream_properties` (flows, T, P), `composition` (component names, mol fractions)
- `utilities` — container for utility definitions, e.g., `utilities.heat_utilities`, `utilities.power_utilities`, `utilities.other_utilities`

When extending the schema, copy the latest version file, increment the version, and follow JSON Schema Draft 7 conventions.

## Code Conventions

### Type hints
Required on all function signatures:
```python
def build_graph(self, flowsheet: Dict[str, Any], target_type: str = 'total_installed_cost') -> Data:
```

### Docstrings
Google-style with `Args:` and `Returns:` sections:
```python
def load_flowsheet(self, file_path: Path) -> Dict[str, Any]:
    """
    Load a single flowsheet JSON file.

    Args:
        file_path: Path to JSON file

    Returns:
        Dictionary containing flowsheet data
    """
```

### Naming
- Classes: `PascalCase` (`FlowsheetDataLoader`, `ProcessGNN`)
- Functions/methods: `snake_case` (`load_all_flowsheets`, `extract_node_features`)
- Private methods: `_` prefix (`_extract_raw_node_features`, `_build_edge_index`)

### Imports
Absolute imports from `src`, never relative:
```python
from src.data.data_loader import FlowsheetDataLoader
from src.models.process_gnn import ProcessGNN
```

Import ordering: standard library, third-party, local.

### `__init__.py` exports
Each subpackage re-exports key classes with `__all__`:
```python
from .process_gnn import ProcessGNN, EnsembleProcessGNN
__all__ = ['ProcessGNN', 'EnsembleProcessGNN']
```

### Logging
Use `logging`, not `print`, for operational messages:
```python
import logging
logger = logging.getLogger(__name__)
logger.info(f"Loaded {len(flowsheets)} flowsheets")
```

### Error handling
Use try-except around file I/O with logging. Raise informative `ValueError` for invalid inputs.

### Models
All models inherit `torch.nn.Module` and implement `forward()`. Follow the `ProcessGNN` pattern: input projection, GAT layers with batch norm, graph pooling, MLP readout.

## How to Extend the Project

### Adding a new GNN model

1. Create a new file in `graph_learning/src/models/`
2. Inherit `torch.nn.Module`; implement `__init__` and `forward(data)` accepting a PyG `Data` object
3. Follow the `ProcessGNN` pattern: input projection -> message-passing layers -> batch norm -> graph pooling -> MLP head
4. The `forward` method must handle `data.x`, `data.edge_index`, `data.edge_attr`, and `data.batch`
5. Export the class from `graph_learning/src/models/__init__.py` with `__all__`
6. Add a model type key to `config.yaml` under `model.type` if needed

### Adding a new data source

1. Export flowsheets to SFF JSON conforming to `schema/schema_v_0.0.2.json`
2. Place JSON files in a new directory under `exported_flowsheets/`
3. Update `config.yaml` `data.flowsheet_dir` to point to the new directory
4. Load with `FlowsheetDataLoader` and verify with `get_statistics()`

### Adding new node or edge features

1. Add extraction logic in `FeatureExtractor._get_unit_features()` (nodes) or `_get_stream_features()` (edges)
2. Add the feature name to `config.yaml` under `features.node_features` or `features.edge_features`
3. Ensure `FeatureExtractor.fit()` handles the new feature (scaler/encoder fitting)
4. Verify output shapes from `FlowsheetGraphBuilder.build_graph()`

### Extending the SFF schema

1. Copy `schema/schema_v_0.0.2.json` to a new version file (e.g., `schema_v_0.0.3.json`)
2. Add or modify fields following JSON Schema Draft 7 conventions
3. Update `export.py` to populate new fields from BioSTEAM
4. Update `metadata.sff_version` references

### Adding a new export source (non-BioSTEAM)

1. Create a new export function in the repository root (e.g., `export_aspen.py`)
2. The function must produce JSON conforming to `schema/schema_v_0.0.2.json`
3. Follow the same output structure as `export_biosteam_flowsheet_sff()` in `export.py`

## Validation

There is no formal test suite. Validate changes by running the demo scripts from within `graph_learning/`:

| Script | What it validates |
|--------|-------------------|
| `python quick_demo.py` | End-to-end pipeline: data loading, graph building, training, evaluation, prediction |
| `python demo_graph_generation.py` | Graph generation models (VAE, link prediction, node type prediction) |
| `python main_pipeline.py` | Full configurable training pipeline with all config options |

When making changes:
- Run `quick_demo.py` as a smoke test after any pipeline change
- Check intermediate outputs: loader statistics, feature matrix shapes, model output dimensions
- Validate exported SFF files against `schema/schema_v_0.0.2.json`

## Key Dependencies

- Python 3.9+
- PyTorch >= 2.0.0, PyTorch Geometric >= 2.3.0
- numpy, pandas, scikit-learn, networkx
- google-cloud-aiplatform, vertexai (Gemini LLM integration)
- matplotlib, seaborn, plotly
- pyyaml, tqdm

Full list: `graph_learning/requirements.txt`

Install:
```bash
cd graph_learning
pip install -r requirements.txt
```
