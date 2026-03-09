# Quick Start Guide: GNN for Chemical Process Analysis

This guide will get you up and running in 10 minutes.

## Step 1: Install Dependencies (2 minutes)

```bash
# Install PyTorch (CPU version)
pip install torch

# Install PyTorch Geometric
pip install torch-geometric

# Install other dependencies
pip install pandas numpy matplotlib seaborn pyyaml scikit-learn tqdm
```

For GPU support, see [PyTorch installation guide](https://pytorch.org/get-started/locally/).

## Step 2: Verify Installation (1 minute)

```bash
python -c "import torch; import torch_geometric; print('✓ All dependencies installed')"
```

## Step 3: Run Quick Demo (5 minutes)

### Option A: Command Line

```bash
# Run the main pipeline
python main_pipeline.py

# This will:
# 1. Load 11 flowsheet files
# 2. Build graph dataset
# 3. Train a GNN model
# 4. Evaluate on test set
# 5. Save results to outputs/
```

### Option B: Jupyter Notebook

```bash
# Start Jupyter
jupyter notebook

# Open example_workflow.ipynb
# Run all cells (Kernel → Restart & Run All)
```

### Option C: Python Script

```python
# quick_demo.py
from src.data.data_loader import FlowsheetDataLoader
from src.data.feature_extractor import FeatureExtractor
from src.data.graph_builder import FlowsheetGraphBuilder
from src.models.process_gnn import ProcessGNN
from src.training.trainer import Trainer
from src.training.utils import train_test_split_graphs

# Load data
loader = FlowsheetDataLoader('exported_flowsheets/bioindustrial_park')
flowsheets = loader.load_all_flowsheets()
print(f"✓ Loaded {len(flowsheets)} flowsheets")

# Build graphs
feature_extractor = FeatureExtractor()
feature_extractor.fit(flowsheets)
graph_builder = FlowsheetGraphBuilder(feature_extractor)
dataset = graph_builder.build_dataset(flowsheets)
print(f"✓ Built {len(dataset)} graphs")

# Split data
train, val, test = train_test_split_graphs(dataset, 0.7, 0.15, 0.15)

# Build and train model
model = ProcessGNN(
    num_node_features=dataset[0].num_node_features,
    num_edge_features=dataset[0].num_edge_features or 0,
    hidden_channels=64,
    num_layers=2,
    heads=4
)
print(f"✓ Created model with {sum(p.numel() for p in model.parameters()):,} parameters")

trainer = Trainer(model, train, val, batch_size=8, learning_rate=0.001)
history = trainer.train(num_epochs=50, verbose=True)
print("✓ Training complete!")

# Evaluate
from src.evaluation.weighted_scorer import WeightedScorer
scorer = WeightedScorer(weight_by='installed_cost')
scores = scorer.calculate_weighted_score(model, test, 'cpu')

print("\n" + "="*60)
print("TEST SET RESULTS:")
print("="*60)
for metric, value in scores.items():
    print(f"{metric:20s}: {value:,.2f}")
```

## Step 4: Explore Results (2 minutes)

After running, check the `outputs/` directory:

```
outputs/
├── checkpoints/                  # Model checkpoints
│   └── checkpoint_epoch_final.pt
├── training_history.json         # Loss, R², MAE over epochs
├── evaluation_results.json       # Detailed test results
└── sample_explanation.txt        # (if Gemini enabled)
```

## What's Next?

### Customize Your Analysis

Edit `config.yaml`:

```yaml
model:
  hidden_channels: 128  # Increase model capacity
  num_gat_layers: 3     # Add more layers
  heads: 8              # More attention heads

training:
  num_epochs: 300       # Train longer
  learning_rate: 0.0005 # Adjust learning rate
```

### Use Your Own Data

1. Export your flowsheet to the SFF JSON format
2. Place files in a new directory (e.g., `my_flowsheets/`)
3. Update `config.yaml`:
   ```yaml
   data:
     flowsheet_dir: "my_flowsheets"
   ```
4. Run: `python main_pipeline.py`

### Add Gemini API Explanations

1. Set up Google Cloud:
   ```bash
   gcloud auth application-default login
   export GOOGLE_CLOUD_PROJECT="your-project-id"
   ```

2. Install:
   ```bash
   pip install google-cloud-aiplatform
   ```

3. Run:
   ```bash
   python main_pipeline.py --use-gemini
   ```

### Experiment with Different Targets

In your code or config, change the target:

```python
# Instead of total cost, predict:
dataset = graph_builder.build_dataset(
    flowsheets,
    target_type='total_power_consumption'  # Or 'total_purchase_cost'
)
```

## Common Issues

### Import Error: No module named 'torch_geometric'

**Solution**: Install PyTorch Geometric properly:
```bash
pip install torch-scatter torch-sparse -f https://data.pyg.org/whl/torch-2.0.0+cpu.html
pip install torch-geometric
```

### CUDA out of memory

**Solution 1**: Reduce batch size in config:
```yaml
training:
  batch_size: 4  # or 2
```

**Solution 2**: Use CPU:
```python
device = 'cpu'
```

### Small dataset warning

With only 11 flowsheets, the model may overfit. To improve:

1. **Use data augmentation**:
   - Add noise to features
   - Randomly drop edges

2. **Simplify the model**:
   ```yaml
   model:
     hidden_channels: 32
     num_gat_layers: 1
   ```

3. **Collect more data**:
   - Export additional flowsheets from BioSTEAM
   - Use transfer learning from similar processes

## Tips for Success

1. **Start simple**: Use default configuration first
2. **Monitor training**: Watch for overfitting (val_loss increasing)
3. **Visualize results**: Use the notebook for interactive exploration
4. **Iterate**: Adjust hyperparameters based on results
5. **Domain knowledge**: Weighted scoring is powerful – choose weights wisely

## Need Help?

- 📖 Read the full documentation: `GNN_PROJECT_README.md`
- 💻 Check example notebook: `example_workflow.ipynb`
- 🐛 Report issues: GitHub issues
- 📧 Contact: sarangbhagwat.developer@gmail.com

---

**You're ready to go! 🚀**

Start with the quick demo above, then explore the notebook for detailed explanations.

