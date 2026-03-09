# GNN-Based Chemical Process Analysis & Generation

A comprehensive framework for applying Graph Neural Networks (GNNs) to **analyze, predict, and generate** chemical process flowsheet structures.

## 🎯 Key Capabilities

### 1. **Property Prediction** (Original)
Predict process properties like total installed cost, energy consumption, etc.
- Graph Attention Networks (GAT)
- Importance-weighted evaluation
- LLM-based explanations (Gemini API)

### 2. **🆕 Graph Generation** (New!)
Generate and predict flowsheet **structures** (nodes and edges):

#### **Flowsheet Generation (GraphVAE)**
- Generate entirely new process flowsheet structures
- Learn from existing designs
- Sample from latent space for novel configurations

#### **Link Prediction**
- Predict which streams should connect units
- Complete partial flowsheets
- Optimize process connectivity

#### **Node Type Prediction**
- Predict types of unit operations
- Equipment selection and optimization
- Process retrofit analysis

---

## 🚀 Quick Start

### Installation

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install torch torch-geometric pandas numpy matplotlib seaborn pyyaml scikit-learn tqdm
pip install google-cloud-aiplatform  # Optional: for Gemini API
pip install jupyter ipykernel  # For notebooks
```

### Property Prediction (Original)

```bash
# Run property prediction pipeline
python main_pipeline.py

# Or use the interactive notebook
jupyter lab
# Open: example_workflow.ipynb
```

### 🆕 Graph Generation (New!)

```bash
# Run graph generation demo
python demo_graph_generation.py
```

**Example output:**
```
[3/5] Training Graph VAE for flowsheet generation...
    ✓ Training complete!
    ✓ Generated 5 flowsheets
      Flowsheet 1: 30 nodes, 87 edges (density: 0.097)
      Flowsheet 2: 30 nodes, 92 edges (density: 0.102)
      
[4/5] Training Link Predictor...
    ✓ Predicted 45 edges
      Precision: 0.867
      Recall: 0.782
      F1 Score: 0.822
```

---

## 📚 Documentation

- **`GNN_PROJECT_README.md`** - Complete original documentation
- **🆕 `GRAPH_GENERATION_GUIDE.md`** - Graph generation guide
- **`QUICK_START_GUIDE.md`** - Get started in 10 minutes
- **`PROJECT_STRUCTURE.md`** - Project organization

---

## 🏗️ Architecture

### Property Prediction Models

```python
from src.models import ProcessGNN

# Train GNN to predict process costs
model = ProcessGNN(
    num_node_features=6,
    num_edge_features=6,
    hidden_channels=64,
    output_dim=1  # Predict scalar value
)
```

### 🆕 Graph Generation Models

```python
from src.models import GraphVAE, LinkPredictionGNN, NodeTypePredictor

# Generate new flowsheet structures
vae = GraphVAE(
    num_node_features=6,
    latent_dim=32
)
new_flowsheets = vae.generate(num_graphs=10, num_nodes=50)

# Predict connections
link_model = LinkPredictionGNN(num_node_features=6)
predicted_edges, probs = link_model.predict_links(partial_flowsheet)

# Predict unit types
node_model = NodeTypePredictor(num_node_features=6, num_node_types=50)
unit_types, probs = node_model.predict(flowsheet)
```

---

## 🎨 Complete Example: Graph Generation

```python
from src.data import FlowsheetDataLoader, FeatureExtractor, FlowsheetGraphBuilder
from src.models import GraphVAE
from src.training import GraphVAETrainer
from src.evaluation import batch_evaluate_generated_flowsheets

# Load data
loader = FlowsheetDataLoader('exported_flowsheets/bioindustrial_park')
flowsheets = loader.load_all_flowsheets()

# Build graphs
feature_extractor = FeatureExtractor()
feature_extractor.fit(flowsheets)
graph_builder = FlowsheetGraphBuilder(feature_extractor)
dataset = graph_builder.build_dataset(flowsheets)

# Train generative model
model = GraphVAE(
    num_node_features=dataset[0].num_node_features,
    num_edge_features=dataset[0].num_edge_features or 0,
    latent_dim=32
)

trainer = GraphVAETrainer(model, dataset[:8], dataset[8:])
history = trainer.train(num_epochs=100)

# Generate new flowsheets
generated_adj, generated_features = model.generate(
    num_graphs=20,
    num_nodes=50
)

# Evaluate generated structures
evaluation = batch_evaluate_generated_flowsheets(
    generated_graphs=generated_pyg_graphs,
    reference_graphs=dataset
)

print(f"Average validity: {evaluation['validity_scores']['overall_validity']['mean']:.2%}")
print(f"DAG ratio: {evaluation['validity_scores']['is_dag']['pass_rate']:.2%}")
```

---

## 📊 Evaluation Metrics

### Property Prediction
- MAE, RMSE, R²
- Weighted metrics (cost-based)
- Per-process breakdown

### 🆕 Graph Structure Evaluation
- **Graph Edit Distance (GED)**
- **Adjacency Matrix Similarity**
- **Structural Metrics**: density, degree distribution, diameter
- **Validity Checks**: DAG, sources, sinks, connectivity
- **Link Prediction**: precision, recall, F1
- **Node Classification**: accuracy, per-class metrics

```python
from src.evaluation import (
    graph_edit_distance,
    structural_metrics,
    flowsheet_validity_score,
    link_prediction_metrics
)

# Compare two flowsheets
ged = graph_edit_distance(graph1, graph2)

# Analyze structure
metrics = structural_metrics(generated_graph)
# → num_nodes, num_edges, density, is_dag, etc.

# Check validity
validity = flowsheet_validity_score(generated_graph)
# → is_dag, has_source, has_sink, no_isolated_nodes, etc.
```

---

## 🎓 Applications

### Property Prediction
- Cost estimation
- Energy consumption prediction
- Process feasibility screening
- Design optimization

### 🆕 Graph Generation
- **Design Space Exploration**: Generate many candidate designs
- **Process Synthesis**: Automatic flowsheet generation
- **Technology Transfer**: Adapt processes to new feedstocks
- **Retrofit Analysis**: Predict process modifications
- **Process Integration**: Identify optimal connections

---

## 📁 Project Structure

```
SFF/
├── src/
│   ├── data/                  # Data loading & graph building
│   ├── models/
│   │   ├── process_gnn.py    # Property prediction (GAT)
│   │   └── graph_generation.py  # 🆕 Generation models
│   ├── training/
│   │   ├── trainer.py        # Property prediction trainer
│   │   └── generation_trainer.py  # 🆕 Generation trainers
│   ├── evaluation/
│   │   ├── metrics.py        # Property prediction metrics
│   │   ├── weighted_scorer.py
│   │   └── graph_metrics.py  # 🆕 Graph structure metrics
│   └── inference/
│       ├── predictor.py
│       └── gemini_explainer.py
│
├── exported_flowsheets/       # 11 bioindustrial process flowsheets
├── config.yaml                # Configuration
├── main_pipeline.py           # Property prediction pipeline
├── demo_graph_generation.py   # 🆕 Generation demo
├── example_workflow.ipynb     # Interactive tutorial
└── Documentation/
    ├── GNN_PROJECT_README.md
    ├── GRAPH_GENERATION_GUIDE.md  # 🆕
    ├── QUICK_START_GUIDE.md
    └── PROJECT_STRUCTURE.md
```

---

## 🔬 Model Architectures

### ProcessGNN (Property Prediction)
- 2-layer Graph Attention Network
- Multi-head attention (4 heads)
- Global pooling → MLP
- Predicts scalar properties

### 🆕 GraphVAE (Generation)
- Encoder: GAT → latent distribution (μ, σ)
- Decoder: latent → adjacency matrix + node features
- VAE loss: reconstruction + KL divergence

### 🆕 LinkPredictionGNN
- Encodes nodes with GAT
- Concatenates node pairs
- Predicts edge probability

### 🆕 NodeTypePredictor
- GAT with graph context
- Multi-class classification
- Per-node predictions

---

## 🎯 Key Features

### Property Prediction
✅ Graph Attention Networks (GAT)  
✅ Multi-head attention for interpretability  
✅ Importance-weighted evaluation  
✅ GPU acceleration  
✅ LLM-based explanations (Gemini)  

### 🆕 Graph Generation
✅ Variational graph generation (GraphVAE)  
✅ Link prediction for connectivity  
✅ Node type prediction  
✅ Structural validity checking  
✅ Chemical engineering constraints  
✅ Batch generation & evaluation  

---

## 📊 Dataset

11 bioindustrial process flowsheets:
- Sugarcane/Corn/Dextrose → Ethanol, 3HP, Succinic acid, TAL
- 30-150 unit operations per flowsheet
- 50-200 process streams
- Detailed cost, energy, and property data

---

## 🤝 Contributing

Contributions welcome! Areas:
- Additional generation models (GraphGAN, autoregressive, etc.)
- Better chemical engineering constraints
- Multi-objective generation
- Conditional generation
- More datasets
- Deployment tools

---

## 📄 License

MIT License - see LICENSE file

---

## 📖 Citation

```bibtex
@software{sff_gnn_2025,
  title={GNN-Based Chemical Process Analysis and Generation},
  author={Bhagwat, Sarang S.},
  year={2025},
  url={https://github.com/sarangbhagwat/sff}
}
```

---

## 📞 Contact

- **Author**: Sarang S. Bhagwat
- **Email**: sarangbhagwat.developer@gmail.com
- **GitHub**: https://github.com/sarangbhagwat/sff

---

**Built with ❤️ for chemical engineering and machine learning**

🔬 Analyze Process Properties | 🎨 Generate Novel Designs | 🔮 Predict Structures

