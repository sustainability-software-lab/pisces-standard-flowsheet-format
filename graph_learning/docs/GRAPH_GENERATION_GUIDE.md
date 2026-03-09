# Graph Generation for Chemical Process Flowsheets

## Overview

This guide explains how to use the GNN models to **generate** and **predict** the structure of chemical process flowsheets, rather than just predicting their properties.

## 🎯 New Capabilities

The project now supports three major graph-based tasks:

### 1. **Graph Generation (GraphVAE)**
Generate entirely new flowsheet structures by sampling from a learned latent space.

```python
from src.models import GraphVAE
from src.training import GraphVAETrainer

# Train a generative model
model = GraphVAE(
    num_node_features=6,
    num_edge_features=6,
    latent_dim=32
)

trainer = GraphVAETrainer(model, train_dataset, val_dataset)
history = trainer.train(num_epochs=100)

# Generate new flowsheets
new_flowsheets = model.generate(num_graphs=5, num_nodes=50)
```

**Use Cases:**
- Design space exploration
- Novel process synthesis
- Process alternatives generation
- Conceptual design

### 2. **Link Prediction**
Predict which streams (edges) should connect between units (nodes).

```python
from src.models import LinkPredictionGNN
from src.training import LinkPredictionTrainer

# Train link predictor
model = LinkPredictionGNN(
    num_node_features=6,
    num_edge_features=6
)

trainer = LinkPredictionTrainer(model, train_dataset, val_dataset)
history = trainer.train(num_epochs=100)

# Predict missing connections
predicted_edges, probabilities = model.predict_links(partial_flowsheet)
```

**Use Cases:**
- Flowsheet completion
- Process connectivity optimization
- Missing stream identification
- Process integration

### 3. **Node Type Prediction**
Predict what type of unit operation each node should be.

```python
from src.models import NodeTypePredictor
from src.training import NodeTypePredictionTrainer

# Train node type predictor
model = NodeTypePredictor(
    num_node_features=6,
    num_edge_features=6,
    num_node_types=50  # Number of unit operation types
)

trainer = NodeTypePredictionTrainer(model, train_dataset, val_dataset)
history = trainer.train(num_epochs=100)

# Predict unit types
predicted_types, probabilities = model.predict(flowsheet)
```

**Use Cases:**
- Unit operation selection
- Equipment type optimization
- Process retrofit analysis

---

## 🏗️ Model Architectures

### GraphVAE (Variational Autoencoder)

**Architecture:**
```
Input Graph
    ↓
Encoder (GAT layers)
    ↓
Latent Space (μ, σ) ← Reparameterization
    ↓
Decoder
    ↓
Output: Adjacency Matrix + Node Features
```

**Key Features:**
- Learns continuous latent representation of flowsheets
- Can interpolate between known designs
- Generates diverse novel structures
- Supports conditional generation

### LinkPredictionGNN

**Architecture:**
```
Input: Partial Graph
    ↓
Node Encoder (GAT)
    ↓
Node Embeddings
    ↓
Edge Decoder (concatenate + MLP)
    ↓
Output: Edge Probabilities for all node pairs
```

**Key Features:**
- Predicts missing connections
- Handles sparse graphs efficiently
- Uses negative sampling for training
- Provides probability scores

### NodeTypePredictor

**Architecture:**
```
Input: Graph with unknown node types
    ↓
GAT Layers (context from neighbors)
    ↓
Node Embeddings
    ↓
Classification Head
    ↓
Output: Node Type Probabilities
```

**Key Features:**
- Multi-class classification
- Uses graph structure for context
- Per-class accuracy reporting

---

## 📊 Evaluation Metrics

### Graph Structure Metrics

```python
from src.evaluation import structural_metrics, flowsheet_validity_score

# Analyze generated flowsheet
metrics = structural_metrics(generated_graph)
print(f"Nodes: {metrics['num_nodes']}")
print(f"Edges: {metrics['num_edges']}")
print(f"Is DAG: {metrics['is_dag']}")  # Important for process flowsheets!
print(f"Density: {metrics['density']}")

# Check validity
validity = flowsheet_validity_score(generated_graph)
print(f"Overall Validity: {validity['overall_validity']:.2%}")
print(f"Has source nodes: {validity['has_source']}")
print(f"Has sink nodes: {validity['has_sink']}")
```

### Similarity Metrics

```python
from src.evaluation import graph_edit_distance, adjacency_matrix_similarity

# Compare two flowsheets
ged = graph_edit_distance(graph1, graph2, normalize=True)
print(f"Graph Edit Distance: {ged:.3f}")

# Compare adjacency matrices
adj_sim = adjacency_matrix_similarity(adj1, adj2)
print(f"Hamming Similarity: {adj_sim['hamming_similarity']:.3f}")
print(f"Cosine Similarity: {adj_sim['cosine_similarity']:.3f}")
```

### Link Prediction Metrics

```python
from src.evaluation import link_prediction_metrics

metrics = link_prediction_metrics(predicted_edges, true_edges, pred_probs)
print(f"Precision: {metrics['precision']:.3f}")
print(f"Recall: {metrics['recall']:.3f}")
print(f"F1 Score: {metrics['f1_score']:.3f}")
```

### Batch Evaluation

```python
from src.evaluation import batch_evaluate_generated_flowsheets

# Comprehensive evaluation
results = batch_evaluate_generated_flowsheets(
    generated_graphs=generated_flowsheets,
    reference_graphs=real_flowsheets
)

print("Structural Properties:")
for key, stats in results['structural_properties'].items():
    print(f"  {key}: {stats['mean']:.2f} ± {stats['std']:.2f}")

print("\nValidity Scores:")
for key, stats in results['validity_scores'].items():
    print(f"  {key}: {stats['mean']:.2%} (pass rate: {stats['pass_rate']:.2%})")
```

---

## 🎨 Complete Example: Flowsheet Generation Pipeline

```python
import torch
from src.data import FlowsheetDataLoader, FeatureExtractor, FlowsheetGraphBuilder
from src.models import GraphVAE, LinkPredictionGNN
from src.training import GraphVAETrainer, LinkPredictionTrainer
from src.evaluation import batch_evaluate_generated_flowsheets

# 1. Load and prepare data
loader = FlowsheetDataLoader('exported_flowsheets/bioindustrial_park')
flowsheets = loader.load_all_flowsheets()

feature_extractor = FeatureExtractor()
feature_extractor.fit(flowsheets)

graph_builder = FlowsheetGraphBuilder(feature_extractor)
dataset = graph_builder.build_dataset(flowsheets)

# 2. Train GraphVAE
vae_model = GraphVAE(
    num_node_features=dataset[0].num_node_features,
    num_edge_features=dataset[0].num_edge_features or 0,
    hidden_channels=64,
    latent_dim=32,
    max_num_nodes=200
)

vae_trainer = GraphVAETrainer(
    model=vae_model,
    train_dataset=dataset[:8],
    val_dataset=dataset[8:],
    batch_size=4,
    learning_rate=0.001
)

print("Training Graph VAE...")
history = vae_trainer.train(num_epochs=50, verbose=True)

# 3. Generate new flowsheets
print("\nGenerating new flowsheets...")
generated_adj, generated_features = vae_model.generate(
    num_graphs=10,
    num_nodes=50,
    device='cpu'
)

# 4. Train Link Predictor (for refinement)
link_model = LinkPredictionGNN(
    num_node_features=dataset[0].num_node_features,
    num_edge_features=dataset[0].num_edge_features or 0,
    hidden_channels=64
)

link_trainer = LinkPredictionTrainer(
    model=link_model,
    train_dataset=dataset[:8],
    val_dataset=dataset[8:],
    batch_size=4
)

print("\nTraining Link Predictor...")
link_history = link_trainer.train(num_epochs=50, verbose=True)

# 5. Refine generated flowsheets
print("\nRefining generated structures...")
for i in range(len(generated_adj)):
    # Convert to PyG Data
    # ... (conversion code)
    
    # Predict better connections
    refined_edges, probs = link_model.predict_links(partial_graph, threshold=0.7)
    print(f"Flowsheet {i}: Predicted {len(refined_edges[0])} connections")

# 6. Evaluate generated flowsheets
print("\nEvaluating generated flowsheets...")
# ... (convert generated structures to PyG Data objects)

evaluation = batch_evaluate_generated_flowsheets(
    generated_graphs=generated_pyg_graphs,
    reference_graphs=dataset
)

print("\n=== EVALUATION RESULTS ===")
print(f"Generated {evaluation['num_generated']} flowsheets")
print(f"\nValidity Scores:")
for metric, stats in evaluation['validity_scores'].items():
    print(f"  {metric}: {stats['mean']:.2%}")

print(f"\nStructural Properties:")
for prop, stats in evaluation['structural_properties'].items():
    print(f"  {prop}: {stats['mean']:.2f} ± {stats['std']:.2f}")
```

---

## 🔬 Advanced Topics

### Conditional Generation

Generate flowsheets with specific characteristics:

```python
# Train with conditions (e.g., target product, feedstock type)
# Encode conditions as additional input to VAE

condition_vector = torch.tensor([
    [1, 0, 0],  # Condition: ethanol production
    [0, 1, 0],  # Condition: 3HP production
])

generated = vae_model.decode(
    vae_model.reparameterize(mu, logvar),
    num_nodes=50,
    condition=condition_vector
)
```

### Multi-Objective Generation

Generate flowsheets optimized for multiple objectives:

```python
from src.models import FlowsheetGenerator

generator = FlowsheetGenerator(
    num_node_features=6,
    num_edge_features=6,
    num_node_types=50
)

# Generate with constraints
outputs = generator.generate(
    num_graphs=10,
    num_nodes=50,  # Target size
    device='cpu'
)
```

### Incremental Generation

Build flowsheets step-by-step:

```python
# Start with core units
core_units = ['Reactor', 'Separator', 'Heater']

# Use link predictor to add connections
for i in range(len(core_units)):
    for j in range(len(core_units)):
        if i != j:
            # Check if connection makes sense
            prob = link_model.predict_single_link(i, j)
            if prob > threshold:
                add_connection(i, j)

# Use node predictor to add supporting units
# ...
```

---

## 🎯 Best Practices

### 1. Data Preparation
- Include diverse flowsheet structures in training data
- Ensure graphs are valid DAGs (directed acyclic graphs)
- Normalize features appropriately

### 2. Training
- Start with simple architectures
- Use learning rate scheduling
- Monitor both reconstruction loss and validity metrics
- Generate samples during training to check quality

### 3. Evaluation
- Always check flowsheet validity (DAG, sources, sinks)
- Compare structural distributions with real flowsheets
- Validate against chemical engineering constraints
- Consider domain-specific rules

### 4. Post-Processing
- Filter invalid structures
- Apply chemical engineering constraints
- Refine with link prediction
- Verify material balances (when features available)

---

## 🚀 Applications

### 1. Design Space Exploration
Generate many candidate designs, evaluate them, select best

### 2. Process Synthesis
Automatic generation of process alternatives for comparison

### 3. Retrofit Analysis
Predict modifications to existing processes

### 4. Technology Transfer
Adapt flowsheets from one feedstock/product to another

### 5. Process Integration
Identify optimal connections for heat/material integration

---

## 📚 References

- **GraphVAE**: Simonovsky & Komodakis, "GraphVAE: Towards Generation of Small Graphs Using Variational Autoencoders"
- **Link Prediction**: Zhang & Chen, "Link Prediction Based on Graph Neural Networks"
- **Process Synthesis**: Douglas, "Conceptual Design of Chemical Processes"

---

## 🤝 Contributing

To add new generation models:

1. Implement in `src/models/graph_generation.py`
2. Add trainer in `src/training/generation_trainer.py`
3. Add metrics in `src/evaluation/graph_metrics.py`
4. Update this documentation

---

**Questions? Check the main README or open an issue!**

