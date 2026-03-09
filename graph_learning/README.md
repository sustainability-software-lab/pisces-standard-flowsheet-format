# Graph Learning for Chemical Process Flowsheets

GNN-based experimentation subpackage for analyzing and predicting properties of chemical process flowsheets using Graph Attention Networks (GATs).

See [GNN_PROJECT_README.md](docs/GNN_PROJECT_README.md) for full documentation.

## Quick Start

```bash
cd graph_learning
pip install -r requirements.txt
python quick_demo.py
```

## Entry Points

| Script | Description |
|--------|-------------|
| `quick_demo.py` | Minimal end-to-end demo |
| `main_pipeline.py` | Full training pipeline (configurable via `config.yaml`) |
| `demo_graph_generation.py` | Graph generation with VAE, link prediction, and node type prediction |
| `example_workflow.ipynb` | Interactive tutorial notebook |
| `graph_generation_deep_dive.ipynb` | Detailed graph generation walkthrough |

## Package Structure

```
src/
├── data/           # Data loading, feature extraction, graph building
├── models/         # GNN architectures (GAT, GraphVAE, link prediction)
├── training/       # Training loops, generation trainers, utilities
├── evaluation/     # Metrics, graph metrics, weighted scoring
├── inference/      # Prediction, Gemini LLM explanations
├── agent/          # Flowsheet design agent, dialogue management
└── deployment/     # Production generator
```

## Configuration

Edit `config.yaml` to customize model architecture, training parameters, and scoring. Data is loaded from `../exported_flowsheets/` (the parent directory's shared data).
