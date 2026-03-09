# GNN-Based Chemical Process Analysis

A comprehensive framework for applying Graph Neural Networks (GNNs) to analyze and predict properties of chemical process flowsheets. This project uses Graph Attention Networks (GATs) to learn from process topology and equipment characteristics.

## 🎯 Overview

This project implements a complete machine learning pipeline for chemical process engineering:

1. **Data Preparation**: Convert flowsheet JSON files to graph representations
2. **Feature Engineering**: Extract and normalize node/edge features
3. **Model Training**: Train Graph Attention Networks (GATs)
4. **Weighted Evaluation**: Assess predictions with importance-based scoring
5. **LLM Interpretation**: Generate natural language explanations using Google's Gemini API

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/sarangbhagwat/sff.git
cd sff

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Basic Usage

#### Option 1: Run the complete pipeline

```bash
python main_pipeline.py --config config.yaml
```

#### Option 2: Use the interactive notebook

```bash
jupyter notebook example_workflow.ipynb
```

#### Option 3: Use as a library

```python
from src.data.data_loader import FlowsheetDataLoader
from src.data.feature_extractor import FeatureExtractor
from src.data.graph_builder import FlowsheetGraphBuilder
from src.models.process_gnn import ProcessGNN
from src.training.trainer import Trainer

# Load data
loader = FlowsheetDataLoader('exported_flowsheets/bioindustrial_park')
flowsheets = loader.load_all_flowsheets()

# Build graphs
feature_extractor = FeatureExtractor()
feature_extractor.fit(flowsheets)

graph_builder = FlowsheetGraphBuilder(feature_extractor)
dataset = graph_builder.build_dataset(flowsheets)

# Train model
model = ProcessGNN(
    num_node_features=dataset[0].num_node_features,
    num_edge_features=dataset[0].num_edge_features,
    hidden_channels=64,
    num_layers=2,
    heads=4
)

trainer = Trainer(model, train_dataset, val_dataset)
history = trainer.train(num_epochs=100)
```

## 📊 Project Structure

```
SFF/
├── src/
│   ├── data/
│   │   ├── data_loader.py          # Load flowsheet JSON files
│   │   ├── feature_extractor.py    # Extract and normalize features
│   │   └── graph_builder.py        # Build PyTorch Geometric graphs
│   ├── models/
│   │   └── process_gnn.py          # GNN model architectures (GAT)
│   ├── training/
│   │   ├── trainer.py              # Training loop with validation
│   │   └── utils.py                # Training utilities
│   ├── evaluation/
│   │   ├── metrics.py              # Evaluation metrics
│   │   └── weighted_scorer.py      # Importance-weighted scoring
│   └── inference/
│       ├── predictor.py            # Make predictions
│       └── gemini_explainer.py     # LLM-based explanations
├── exported_flowsheets/
│   └── bioindustrial_park/         # Example flowsheet data
├── config.yaml                      # Configuration file
├── main_pipeline.py                 # Main training script
├── example_workflow.ipynb           # Interactive tutorial
└── requirements.txt                 # Python dependencies
```

## 🔧 Configuration

Edit `config.yaml` to customize the workflow:

```yaml
# Model architecture
model:
  hidden_channels: 64
  num_gat_layers: 2
  heads: 4
  dropout: 0.2

# Training settings
training:
  batch_size: 8
  learning_rate: 0.001
  num_epochs: 200
  validation_split: 0.2
  early_stopping_patience: 20

# Weighted scoring
scoring:
  weight_by: "installed_cost"  # Options: installed_cost, power_consumption, heat_duty
```

## 📈 Features

### Data Processing
- ✅ Load flowsheets from standard JSON format
- ✅ Automatic feature extraction and normalization
- ✅ Handle variable-sized graphs
- ✅ Support for node and edge features

### Model Architecture
- ✅ Graph Attention Networks (GAT/GATv2)
- ✅ Multi-head attention mechanism
- ✅ Batch normalization and dropout
- ✅ Flexible pooling strategies (mean, max, add)
- ✅ Support for edge features

### Training & Evaluation
- ✅ Early stopping with patience
- ✅ Model checkpointing
- ✅ Training history tracking
- ✅ Multiple evaluation metrics (MAE, MSE, R², MAPE)
- ✅ Importance-weighted scoring

### Inference & Interpretation
- ✅ Prediction with uncertainty estimates (ensemble mode)
- ✅ Attention weight visualization
- ✅ Important component identification
- ✅ Natural language explanations (via Gemini API)
- ✅ Cost optimization suggestions

## 🎓 Example Results

After training on the bioindustrial park dataset:

```
Test Set Metrics:
==================================================
MAE                 : 2,450,000.00
RMSE                : 3,200,000.00
R2                  : 0.89
Weighted_MAE        : 2,100,000.00
```

## 🌐 Gemini API Integration

To use Google's Gemini API for natural language explanations:

1. Set up Google Cloud credentials:
```bash
gcloud auth application-default login
```

2. Set environment variable:
```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
```

3. Run with Gemini enabled:
```bash
python main_pipeline.py --use-gemini
```

Example explanation output:
```
The GNN model predicts a total installed cost of $45.2M for this 
sugarcane-to-ethanol process. The prediction is within 5% of the 
actual cost, indicating good model performance. Key cost drivers 
include the fermentation reactors (Unit 15-18) and the distillation 
columns (Units 25-28). The model's attention mechanism identifies 
the sugar-rich stream from crushing (Units 5→15) as critical, 
suggesting optimization efforts should focus on maximizing sugar 
extraction efficiency.
```

## 📚 Advanced Usage

### Custom Weight Functions

```python
from src.evaluation.weighted_scorer import WeightedScorer

def custom_weight_fn(data):
    # Weight by both cost and energy consumption
    node_features = data.x.numpy()
    cost_weights = node_features[:, 1]  # Installed cost
    energy_weights = node_features[:, 3]  # Power consumption
    
    combined = 0.7 * cost_weights + 0.3 * energy_weights
    return combined / combined.sum()

scorer = WeightedScorer(weight_by='custom', weight_function=custom_weight_fn)
```

### Ensemble Models

```python
from src.models.process_gnn import EnsembleProcessGNN

ensemble = EnsembleProcessGNN(
    num_models=5,
    num_node_features=dataset[0].num_node_features,
    hidden_channels=64
)

# Training and prediction with uncertainty estimates
mean_pred, std_pred = ensemble(data)
```

### Multi-task Learning

Modify the model to predict multiple targets simultaneously:

```python
model = ProcessGNN(
    num_node_features=num_node_features,
    num_edge_features=num_edge_features,
    hidden_channels=64,
    output_dim=3  # Predict cost, energy, and emissions
)
```

## 🔬 Methodology

### Graph Representation

- **Nodes**: Unit operations (reactors, separators, heat exchangers, etc.)
- **Node Features**: Equipment type, installed cost, power consumption, heat duty, flow rate
- **Edges**: Material streams connecting units
- **Edge Features**: Mass flow, temperature, pressure, composition
- **Target**: Total installed cost (or other process metrics)

### Graph Attention Networks

GATs learn to weight the importance of neighboring nodes:

```
h_i' = σ(Σ α_ij W h_j)
```

where α_ij are learned attention coefficients indicating the importance of node j to node i.

### Weighted Scoring

Standard metrics may not reflect engineering priorities. We weight errors by component importance:

```
Weighted MAE = Σ w_i |y_i - ŷ_i| / Σ w_i
```

where w_i is the importance weight (e.g., equipment cost) for process i.

## 📊 Datasets

The project includes 11 bioindustrial process flowsheets:

- Corn → 3-Hydroxypropionate (3HP) → Acrylic Acid
- Corn → Succinic Acid
- Dextrose → 3HP → Acrylic Acid
- Dextrose → Succinic Acid
- Dextrose → Triacetic Acid Lactone (TAL)
- Dextrose → TAL → Ketoacids (KS)
- Sugarcane → 3HP → Acrylic Acid
- Sugarcane → Ethanol
- Sugarcane → Succinic Acid
- Sugarcane → TAL
- Sugarcane → TAL → KS

Each flowsheet includes:
- 30-150 unit operations
- 50-200 process streams
- Detailed cost and utility data
- Thermodynamic properties

## 🤝 Contributing

Contributions are welcome! Areas for improvement:

- Additional GNN architectures (GraphSAGE, GIN, etc.)
- Uncertainty quantification methods
- Graph generation and optimization
- Additional datasets
- Deployment tools

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 📖 Citation

If you use this code in your research, please cite:

```bibtex
@software{sff_gnn_2025,
  title={GNN-Based Chemical Process Analysis},
  author={Bhagwat, Sarang S.},
  year={2025},
  url={https://github.com/sarangbhagwat/sff}
}
```

## 📞 Contact

- **Author**: Sarang S. Bhagwat
- **Email**: sarangbhagwat.developer@gmail.com
- **GitHub**: https://github.com/sarangbhagwat/sff

## 🙏 Acknowledgments

- PyTorch Geometric for excellent GNN tools
- Google for the Gemini API
- The BioSTEAM team for process simulation capabilities
- The chemical engineering community for domain expertise

---

**Built with ❤️ for the chemical engineering and ML communities**

