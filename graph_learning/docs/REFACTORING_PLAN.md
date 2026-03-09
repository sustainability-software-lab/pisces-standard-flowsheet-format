# 🏗️ Repository Refactoring Plan

## Executive Summary

**Goal**: Reorganize the SFF repository into a clean, modular structure with proper separation of concerns, supporting both research/exploration and production deployment.

**Scope**: Complete restructuring of files and directories
**Duration**: 2-3 hours of careful reorganization
**Risk**: Low (version control protects against issues)

---

## Current State Analysis

### 📊 Current Structure Issues

```
Current Problems:
❌ 15+ documentation files at root level
❌ Notebooks mixed at root level  
❌ Scripts scattered (demo, quick_demo, main_pipeline, etc.)
❌ No clear research vs production separation
❌ Agent code mixed with data processing
❌ No tests directory
❌ Config files at root
❌ Outputs and data directories mixed
❌ No clear module boundaries
```

### Current Directory Tree (Simplified)

```
SFF/
├── src/                          # Mixed concerns
│   ├── data/                     # Data processing
│   ├── models/                   # Model definitions
│   ├── training/                 # Training code
│   ├── evaluation/               # Metrics
│   ├── inference/                # Prediction
│   ├── deployment/               # Production
│   └── agent/                    # Agent (just added)
├── exported_flowsheets/          # Raw data
├── data/processed/               # Processed data
├── outputs/checkpoints/          # Model artifacts
├── schema/                       # SFF schema
├── *.ipynb                       # 2 notebooks at root
├── *.py                          # 5+ scripts at root
├── *.md                          # 15+ docs at root
├── config.yaml                   # Config at root
└── venv/                         # Virtual environment
```

**Total Files**: ~50 at root level  
**Total Directories**: ~10 with unclear purpose  
**Module Depth**: Flat, no hierarchy

---

## Proposed Structure

### 🎯 Design Principles

1. **Separation of Concerns**: Research, production, utilities clearly separated
2. **Module Hierarchy**: Logical grouping with submodules
3. **Standard Layout**: Follows Python best practices
4. **Scalability**: Easy to add new components
5. **Discoverability**: Clear where things belong
6. **Documentation**: Organized and accessible

### 📁 New Directory Structure

```
SFF/
├── 📦 flowsheet_design/              # Main Python package (renamed from src)
│   ├── __init__.py
│   │
│   ├── 📊 data/                      # Data processing module
│   │   ├── __init__.py
│   │   ├── loaders/                  # Data loading
│   │   │   ├── __init__.py
│   │   │   └── sff_loader.py         # (was data_loader.py)
│   │   ├── preprocessing/            # Feature extraction
│   │   │   ├── __init__.py
│   │   │   ├── feature_extractor.py
│   │   │   └── graph_builder.py
│   │   └── utils/                    # Data utilities
│   │       ├── __init__.py
│   │       └── validators.py
│   │
│   ├── 🧠 models/                    # Neural network models
│   │   ├── __init__.py
│   │   ├── gnn/                      # GNN architectures
│   │   │   ├── __init__.py
│   │   │   ├── process_gnn.py       # Property prediction
│   │   │   └── graph_vae.py         # Structure generation (from graph_generation.py)
│   │   └── components/               # Reusable model components
│   │       ├── __init__.py
│   │       ├── encoders.py
│   │       └── decoders.py
│   │
│   ├── 🏋️ training/                  # Training infrastructure
│   │   ├── __init__.py
│   │   ├── trainers/                 # Training loops
│   │   │   ├── __init__.py
│   │   │   ├── base_trainer.py      # Base class
│   │   │   ├── property_trainer.py  # (was trainer.py)
│   │   │   └── generation_trainer.py
│   │   ├── callbacks/                # Training callbacks
│   │   │   ├── __init__.py
│   │   │   ├── early_stopping.py
│   │   │   └── checkpointing.py
│   │   └── utils.py
│   │
│   ├── 📏 evaluation/                # Evaluation metrics
│   │   ├── __init__.py
│   │   ├── property_metrics.py      # (was metrics.py)
│   │   ├── graph_metrics.py
│   │   └── weighted_scorer.py
│   │
│   ├── 🔮 inference/                 # Inference and prediction
│   │   ├── __init__.py
│   │   ├── predictor.py
│   │   └── explainers/
│   │       ├── __init__.py
│   │       └── gemini_explainer.py
│   │
│   ├── 🚀 deployment/                # Production deployment
│   │   ├── __init__.py
│   │   ├── generators/               # Production generators
│   │   │   ├── __init__.py
│   │   │   └── flowsheet_generator.py  # (was production_generator.py)
│   │   ├── api/                      # API endpoints (future)
│   │   │   ├── __init__.py
│   │   │   └── routes.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       └── validators.py
│   │
│   ├── 🤖 agent/                     # Autonomous design agent
│   │   ├── __init__.py
│   │   ├── core/                     # Core agent logic
│   │   │   ├── __init__.py
│   │   │   ├── flowsheet_agent.py
│   │   │   └── dialogue_manager.py
│   │   ├── tools/                    # Agent tools
│   │   │   ├── __init__.py
│   │   │   ├── requirement_extractor.py
│   │   │   ├── structure_generator.py
│   │   │   ├── sff_generator.py
│   │   │   └── design_validator.py
│   │   ├── knowledge/                # Knowledge base
│   │   │   ├── __init__.py
│   │   │   ├── flowsheet_retriever.py
│   │   │   ├── unit_library.py
│   │   │   └── pattern_matcher.py
│   │   └── interfaces/               # UI interfaces
│   │       ├── __init__.py
│   │       ├── cli.py
│   │       ├── web_app.py
│   │       └── api_server.py
│   │
│   ├── 🔧 utils/                     # Shared utilities
│   │   ├── __init__.py
│   │   ├── config.py                 # Configuration management
│   │   ├── logging.py                # Logging setup
│   │   └── io.py                     # File I/O utilities
│   │
│   └── 📋 schemas/                   # Data schemas
│       ├── __init__.py
│       ├── sff_schema.py            # Python schema definitions
│       └── validators.py            # Schema validation
│
├── 📓 notebooks/                     # Jupyter notebooks (research)
│   ├── 01_exploration/              # Initial data exploration
│   │   └── data_exploration.ipynb
│   ├── 02_graph_learning/           # GNN experiments
│   │   ├── example_workflow.ipynb
│   │   └── graph_generation_deep_dive.ipynb
│   ├── 03_agent_development/        # Agent prototyping
│   │   └── agent_prototype.ipynb
│   └── README.md                    # Notebook guide
│
├── 🧪 tests/                        # Test suite
│   ├── __init__.py
│   ├── unit/                        # Unit tests
│   │   ├── test_data_loaders.py
│   │   ├── test_models.py
│   │   ├── test_trainers.py
│   │   └── test_agent.py
│   ├── integration/                 # Integration tests
│   │   ├── test_training_pipeline.py
│   │   ├── test_generation_pipeline.py
│   │   └── test_agent_workflow.py
│   ├── fixtures/                    # Test fixtures
│   │   ├── sample_flowsheets/
│   │   └── mock_models/
│   └── conftest.py                  # Pytest configuration
│
├── 🛠️ scripts/                      # Utility scripts
│   ├── data/                        # Data processing scripts
│   │   ├── export_flowsheets.py    # (was export.py)
│   │   └── build_knowledge_base.py
│   ├── training/                    # Training scripts
│   │   ├── train_property_model.py # (was main_pipeline.py)
│   │   └── train_generation_model.py
│   ├── demos/                       # Demo scripts
│   │   ├── quick_demo.py
│   │   ├── demo_graph_generation.py
│   │   └── demo_agent.py
│   └── utils/                       # Utility scripts
│       ├── setup_environment.py
│       └── migrate_data.py
│
├── 📚 docs/                          # Documentation
│   ├── README.md                     # Docs index
│   ├── getting_started/             # Getting started guides
│   │   ├── installation.md
│   │   ├── quick_start.md
│   │   └── configuration.md
│   ├── user_guides/                 # User documentation
│   │   ├── data_preparation.md
│   │   ├── training_models.md
│   │   ├── generating_flowsheets.md
│   │   └── using_agent.md
│   ├── api_reference/               # API documentation
│   │   ├── data_module.md
│   │   ├── models_module.md
│   │   ├── training_module.md
│   │   └── agent_module.md
│   ├── architecture/                # Architecture docs
│   │   ├── system_overview.md
│   │   ├── data_flow.md
│   │   ├── model_architectures.md
│   │   └── agent_design.md
│   ├── development/                 # Development docs
│   │   ├── contributing.md
│   │   ├── code_standards.md
│   │   └── testing_guide.md
│   ├── research/                    # Research documentation
│   │   ├── graph_learning_results.md
│   │   ├── generation_experiments.md
│   │   └── agent_strategy.md
│   └── deployment/                  # Deployment docs
│       ├── production_guide.md
│       ├── api_deployment.md
│       └── scaling.md
│
├── ⚙️ configs/                      # Configuration files
│   ├── default.yaml                 # (was config.yaml)
│   ├── training/                    # Training configs
│   │   ├── property_prediction.yaml
│   │   └── structure_generation.yaml
│   ├── deployment/                  # Deployment configs
│   │   ├── production.yaml
│   │   └── api_config.yaml
│   └── agent/                       # Agent configs
│       ├── knowledge_base.yaml
│       └── llm_config.yaml
│
├── 💾 data/                          # Data directory
│   ├── raw/                          # Raw data
│   │   └── flowsheets/              # (was exported_flowsheets)
│   │       └── bioindustrial_park/
│   ├── processed/                    # Processed data
│   │   ├── features/
│   │   ├── graphs/
│   │   └── embeddings/
│   ├── knowledge_base/              # Agent knowledge base
│   │   ├── flowsheet_index/
│   │   ├── unit_library/
│   │   └── patterns/
│   └── external/                    # External datasets
│
├── 🎯 models/                        # Saved models
│   ├── property_prediction/
│   │   ├── checkpoints/
│   │   └── best_model.pt
│   ├── structure_generation/
│   │   ├── checkpoints/
│   │   └── graph_vae_best.pt
│   └── README.md                    # Model documentation
│
├── 📊 outputs/                       # Outputs and results
│   ├── experiments/                  # Experiment results
│   │   ├── property_prediction/
│   │   └── structure_generation/
│   ├── generated_flowsheets/        # Generated designs
│   ├── evaluations/                  # Evaluation results
│   └── visualizations/              # Plots and figures
│
├── 📄 schemas/                       # Data schemas (JSON)
│   ├── sff_schema_v0.0.1.json
│   ├── sff_schema_v0.1.0.json       # Updated version
│   └── validation_rules.json
│
├── 🌐 .github/                       # GitHub configuration
│   ├── workflows/                    # CI/CD pipelines
│   │   ├── tests.yml
│   │   └── docs.yml
│   └── ISSUE_TEMPLATE/
│
├── 📝 Project Root Files             # Keep at root
│   ├── README.md                     # Main README
│   ├── LICENSE
│   ├── requirements.txt              # Dependencies
│   ├── requirements-dev.txt          # Dev dependencies
│   ├── setup.py                      # Package setup
│   ├── pyproject.toml               # Modern Python config
│   ├── .gitignore
│   ├── .env.example                 # Environment template
│   └── Makefile                     # Common commands
│
└── 🔒 .venv/                        # Virtual environment (gitignored)
```

---

## Detailed Refactoring Steps

### Phase 1: Create New Structure

**Step 1.1: Create New Directories**

```bash
# Main package rename
mkdir -p flowsheet_design

# Data module
mkdir -p flowsheet_design/data/{loaders,preprocessing,utils}

# Models module
mkdir -p flowsheet_design/models/{gnn,components}

# Training module
mkdir -p flowsheet_design/training/{trainers,callbacks}

# Evaluation module (already exists, reorganize)
mkdir -p flowsheet_design/evaluation

# Inference module
mkdir -p flowsheet_design/inference/explainers

# Deployment module
mkdir -p flowsheet_design/deployment/{generators,api,utils}

# Agent module (reorganize existing)
mkdir -p flowsheet_design/agent/{core,tools,knowledge,interfaces}

# Utils and schemas
mkdir -p flowsheet_design/{utils,schemas}

# Notebooks
mkdir -p notebooks/{01_exploration,02_graph_learning,03_agent_development}

# Tests
mkdir -p tests/{unit,integration,fixtures}

# Scripts
mkdir -p scripts/{data,training,demos,utils}

# Documentation
mkdir -p docs/{getting_started,user_guides,api_reference,architecture,development,research,deployment}

# Configs
mkdir -p configs/{training,deployment,agent}

# Data directories
mkdir -p data/{raw/flowsheets,processed/{features,graphs,embeddings},knowledge_base/{flowsheet_index,unit_library,patterns},external}

# Models directory
mkdir -p models/{property_prediction/checkpoints,structure_generation/checkpoints}

# Outputs
mkdir -p outputs/{experiments/{property_prediction,structure_generation},generated_flowsheets,evaluations,visualizations}
```

**Step 1.2: Create __init__.py Files**

```bash
# Create all necessary __init__.py files
touch flowsheet_design/__init__.py
touch flowsheet_design/data/__init__.py
touch flowsheet_design/data/loaders/__init__.py
touch flowsheet_design/data/preprocessing/__init__.py
touch flowsheet_design/data/utils/__init__.py
# ... (continue for all packages)
```

---

### Phase 2: Move and Rename Files

#### 2.1 Data Module

```bash
# Move data loading
mv src/data/data_loader.py flowsheet_design/data/loaders/sff_loader.py

# Move preprocessing
mv src/data/feature_extractor.py flowsheet_design/data/preprocessing/
mv src/data/graph_builder.py flowsheet_design/data/preprocessing/

# Update __init__.py
```

#### 2.2 Models Module

```bash
# Split graph_generation.py into components
# Create flowsheet_design/models/gnn/graph_vae.py with GraphVAE
# Create flowsheet_design/models/gnn/link_prediction.py with LinkPredictionGNN
# Create flowsheet_design/models/gnn/node_predictor.py with NodeTypePredictor

# Move property prediction model
mv src/models/process_gnn.py flowsheet_design/models/gnn/
```

#### 2.3 Training Module

```bash
# Move trainers
mv src/training/trainer.py flowsheet_design/training/trainers/property_trainer.py
mv src/training/generation_trainer.py flowsheet_design/training/trainers/

# Extract callbacks from trainers into separate files
# Move utils
mv src/training/utils.py flowsheet_design/training/
```

#### 2.4 Evaluation Module

```bash
# Move evaluation files
mv src/evaluation/metrics.py flowsheet_design/evaluation/property_metrics.py
mv src/evaluation/graph_metrics.py flowsheet_design/evaluation/
mv src/evaluation/weighted_scorer.py flowsheet_design/evaluation/
```

#### 2.5 Inference Module

```bash
# Move inference files
mv src/inference/predictor.py flowsheet_design/inference/
mv src/inference/gemini_explainer.py flowsheet_design/inference/explainers/
```

#### 2.6 Deployment Module

```bash
# Move deployment files
mv src/deployment/production_generator.py flowsheet_design/deployment/generators/flowsheet_generator.py
```

#### 2.7 Agent Module

```bash
# Reorganize agent
mv src/agent/flowsheet_agent.py flowsheet_design/agent/core/
mv src/agent/dialogue_manager.py flowsheet_design/agent/core/

# Move tools (already in place, just ensure structure)
mv src/agent/tools/* flowsheet_design/agent/tools/

# Create knowledge base files
# (Tools exist as stubs, keep for now)
```

#### 2.8 Notebooks

```bash
# Move notebooks
mv example_workflow.ipynb notebooks/02_graph_learning/
mv graph_generation_deep_dive.ipynb notebooks/02_graph_learning/
```

#### 2.9 Scripts

```bash
# Move scripts
mv export.py scripts/data/export_flowsheets.py
mv main_pipeline.py scripts/training/train_property_model.py
mv quick_demo.py scripts/demos/
mv demo_graph_generation.py scripts/demos/
```

#### 2.10 Documentation

```bash
# Organize documentation
mv QUICK_START_GUIDE.md docs/getting_started/quick_start.md
mv GNN_PROJECT_README.md docs/README.md
mv FLOWSHEET_DESIGN_AGENT_STRATEGY.md docs/research/agent_strategy.md
mv AGENT_IMPLEMENTATION_ROADMAP.md docs/development/agent_roadmap.md
mv PRODUCTION_DEPLOYMENT.md docs/deployment/production_guide.md
mv GRAPH_GENERATION_GUIDE.md docs/user_guides/generating_flowsheets.md
# ... move other docs appropriately
```

#### 2.11 Configs

```bash
# Move config
mv config.yaml configs/default.yaml
```

#### 2.12 Data

```bash
# Move data
mv exported_flowsheets data/raw/flowsheets

# Ensure processed data is in right place
# (already at data/processed, just ensure structure)
```

#### 2.13 Models

```bash
# Move model checkpoints
mv outputs/checkpoints/* models/structure_generation/checkpoints/
```

#### 2.14 Schemas

```bash
# Move schema
mv schema/schema_v_0.0.1.json schemas/sff_schema_v0.0.1.json
```

---

### Phase 3: Update Import Paths

**Critical**: All imports need to be updated

**Strategy**: Use find-and-replace with care

**Examples**:

```python
# OLD
from src.data.data_loader import FlowsheetDataLoader
from src.models.graph_generation import GraphVAE
from src.training.trainer import PropertyPredictionTrainer

# NEW
from flowsheet_design.data.loaders import FlowsheetDataLoader  
from flowsheet_design.models.gnn import GraphVAE
from flowsheet_design.training.trainers import PropertyPredictionTrainer
```

**Files to Update**:
- All Python files in flowsheet_design/
- All notebooks
- All scripts
- Test files
- Config files (if they reference modules)

---

### Phase 4: Create New Root Files

#### 4.1 setup.py

```python
from setuptools import setup, find_packages

setup(
    name="flowsheet-design",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        # Read from requirements.txt
    ],
    extras_require={
        'dev': [
            'pytest>=7.0',
            'pytest-cov>=4.0',
            'black>=23.0',
            'flake8>=6.0',
            'mypy>=1.0',
        ]
    },
    python_requires='>=3.9',
)
```

#### 4.2 pyproject.toml

```toml
[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "flowsheet-design"
version = "0.1.0"
description = "AI-powered chemical process flowsheet design"
readme = "README.md"
requires-python = ">=3.9"

[tool.black]
line-length = 100
target-version = ['py39', 'py310', 'py311']

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]

[tool.mypy]
python_version = "3.9"
warn_return_any = true
warn_unused_configs = true
```

#### 4.3 Makefile

```makefile
.PHONY: install test lint format clean

install:
	pip install -e .
	pip install -e .[dev]

test:
	pytest tests/ -v --cov=flowsheet_design

lint:
	flake8 flowsheet_design tests
	mypy flowsheet_design

format:
	black flowsheet_design tests scripts
	isort flowsheet_design tests scripts

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf htmlcov

docs:
	cd docs && make html

run-tests:
	pytest tests/ -v

run-agent:
	python scripts/demos/demo_agent.py
```

#### 4.4 .env.example

```bash
# Environment configuration template
# Copy to .env and fill in your values

# Data paths
DATA_DIR=data/raw/flowsheets
PROCESSED_DATA_DIR=data/processed
KNOWLEDGE_BASE_DIR=data/knowledge_base

# Model paths
MODEL_DIR=models
CHECKPOINT_DIR=models/structure_generation/checkpoints

# LLM Configuration (for agent)
OPENAI_API_KEY=your_openai_key_here
LLM_MODEL=gpt-4

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/flowsheet_design.log

# Deployment
DEPLOYMENT_ENV=development
API_HOST=0.0.0.0
API_PORT=8000
```

---

### Phase 5: Update Configuration

#### configs/default.yaml

```yaml
# Default configuration

project:
  name: "Flowsheet Design System"
  version: "0.1.0"

paths:
  data_dir: "data/raw/flowsheets"
  processed_dir: "data/processed"
  models_dir: "models"
  outputs_dir: "outputs"
  knowledge_base_dir: "data/knowledge_base"

data:
  flowsheet_dir: "data/raw/flowsheets/bioindustrial_park"
  features_dir: "data/processed/features"
  graphs_dir: "data/processed/graphs"

models:
  property_prediction:
    checkpoint_dir: "models/property_prediction/checkpoints"
    best_model: "models/property_prediction/best_model.pt"
  structure_generation:
    checkpoint_dir: "models/structure_generation/checkpoints"
    best_model: "models/structure_generation/graph_vae_best.pt"

training:
  batch_size: 32
  learning_rate: 0.001
  num_epochs: 100
  device: "cpu"  # or "cuda"

evaluation:
  metrics:
    - "mae"
    - "r2"
    - "mse"

logging:
  level: "INFO"
  file: "logs/flowsheet_design.log"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

---

### Phase 6: Create Tests Structure

#### tests/conftest.py

```python
"""Pytest configuration and fixtures."""

import pytest
import torch
from pathlib import Path

@pytest.fixture
def sample_flowsheet():
    """Load a sample flowsheet for testing."""
    # Implementation
    pass

@pytest.fixture
def mock_model():
    """Create a mock model for testing."""
    # Implementation
    pass

@pytest.fixture
def temp_data_dir(tmp_path):
    """Create temporary data directory for tests."""
    return tmp_path / "test_data"
```

#### tests/unit/test_data_loaders.py

```python
"""Unit tests for data loaders."""

import pytest
from flowsheet_design.data.loaders import FlowsheetDataLoader

def test_flowsheet_loader_init():
    """Test FlowsheetDataLoader initialization."""
    # Test implementation
    pass

def test_load_flowsheet(sample_flowsheet):
    """Test loading a single flowsheet."""
    # Test implementation
    pass
```

---

### Phase 7: Update Documentation

#### docs/README.md

```markdown
# Flowsheet Design System Documentation

Welcome to the Flowsheet Design System documentation!

## Documentation Structure

- **[Getting Started](getting_started/)** - Installation and quick start
- **[User Guides](user_guides/)** - How to use the system
- **[API Reference](api_reference/)** - Detailed API documentation
- **[Architecture](architecture/)** - System design and architecture
- **[Development](development/)** - Contributing and development guides
- **[Research](research/)** - Research notes and experiments
- **[Deployment](deployment/)** - Production deployment guides

## Quick Links

- [Installation Guide](getting_started/installation.md)
- [Quick Start Tutorial](getting_started/quick_start.md)
- [Training Models](user_guides/training_models.md)
- [Using the Agent](user_guides/using_agent.md)
- [API Reference](api_reference/)

## Overview

This system provides:

1. **Data Processing**: Load and process flowsheet data
2. **Model Training**: Train GNN models for property prediction and structure generation
3. **Generation**: Generate new flowsheet designs
4. **Agent**: Autonomous flowsheet design through conversation
5. **Deployment**: Production-ready deployment tools
```

---

### Phase 8: Migration Script

Create `scripts/utils/migrate_to_new_structure.py`:

```python
"""
Migration script to reorganize repository.

This script automates the refactoring process.
"""

import shutil
from pathlib import Path

# Define migrations
MIGRATIONS = {
    'src/data/data_loader.py': 'flowsheet_design/data/loaders/sff_loader.py',
    'src/data/feature_extractor.py': 'flowsheet_design/data/preprocessing/feature_extractor.py',
    # ... all other mappings
}

def migrate():
    """Run migration."""
    for old_path, new_path in MIGRATIONS.items():
        old = Path(old_path)
        new = Path(new_path)
        
        if old.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
            print(f"✓ Moved {old} → {new}")
        else:
            print(f"⚠ Skipped {old} (not found)")

if __name__ == "__main__":
    migrate()
```

---

## Benefits of New Structure

### ✅ Improved Organization

| Aspect | Before | After |
|--------|--------|-------|
| **Root files** | ~50 files | ~10 essential files |
| **Module depth** | Flat (2 levels) | Hierarchical (4 levels) |
| **Concerns** | Mixed | Clearly separated |
| **Discoverability** | Difficult | Intuitive |

### ✅ Better Scalability

- Easy to add new models, trainers, or agent tools
- Clear where new features belong
- Isolated concerns minimize conflicts

### ✅ Enhanced Maintainability

- Logical grouping reduces cognitive load
- Standard structure familiar to Python developers
- Clear separation of research vs production

### ✅ Professional Quality

- Follows Python best practices
- Ready for open source release
- Easy for new contributors
- Supports proper packaging and distribution

---

## Migration Checklist

### Pre-Migration
- [ ] Commit all current changes
- [ ] Create backup branch: `git checkout -b pre-refactor-backup`
- [ ] Tag current state: `git tag v0.0.1-pre-refactor`
- [ ] Review refactoring plan
- [ ] Identify any custom local changes

### Migration
- [ ] Create new directory structure
- [ ] Move files to new locations
- [ ] Update all import paths
- [ ] Update config files
- [ ] Create new root files (setup.py, etc.)
- [ ] Update documentation
- [ ] Create tests structure

### Post-Migration
- [ ] Run import checks: `python -c "import flowsheet_design"`
- [ ] Run all tests: `make test`
- [ ] Run linting: `make lint`
- [ ] Test notebooks (run all cells)
- [ ] Test scripts (run demos)
- [ ] Update README.md
- [ ] Commit refactored code
- [ ] Tag new state: `git tag v0.1.0-refactored`

---

## Risk Mitigation

### Backup Strategy
```bash
# Before starting
git checkout -b refactor-v0.1.0
git branch pre-refactor-backup  # Safety backup

# Work on refactor branch
# If something goes wrong:
git checkout main  # Return to original
```

### Testing Strategy
1. Test each module independently after moving
2. Run full test suite after all moves
3. Test all notebooks end-to-end
4. Test all scripts
5. Verify agent still works

### Rollback Plan
If refactoring causes issues:
```bash
git checkout pre-refactor-backup
# Or
git reset --hard v0.0.1-pre-refactor
```

---

## Estimated Time

- **Phase 1-2**: 30 minutes (create structure, move files)
- **Phase 3**: 60 minutes (update imports)
- **Phase 4-5**: 20 minutes (new root files, configs)
- **Phase 6-7**: 20 minutes (tests, docs)
- **Phase 8**: 10 minutes (migration script)
- **Testing**: 30 minutes (verify everything works)

**Total**: 2.5-3 hours

---

## Post-Refactor Improvements

After refactoring, you'll be able to easily:

1. **Install as package**: `pip install -e .`
2. **Run tests**: `make test`
3. **Format code**: `make format`
4. **Build docs**: `make docs`
5. **Import anywhere**: `from flowsheet_design.models.gnn import GraphVAE`

---

## Next Steps After Refactoring

1. Set up CI/CD with GitHub Actions
2. Add comprehensive test coverage
3. Generate API documentation with Sphinx
4. Create Docker containers for deployment
5. Set up pre-commit hooks
6. Add type hints throughout
7. Implement logging consistently
8. Create contribution guidelines

---

## Questions to Resolve

Before proceeding, please confirm:

1. **Package name**: Is "flowsheet_design" acceptable, or prefer "sff", "flowsheet", or other?
2. **Python version**: Target Python 3.9+? Or need older support?
3. **Testing framework**: Pytest OK, or prefer unittest?
4. **Documentation**: Sphinx or MkDocs for API docs?
5. **Backwards compatibility**: Need to maintain old import paths?

---

## Approval Required

Please review this refactoring plan and approve:

- [ ] Overall structure looks good
- [ ] Module names are appropriate
- [ ] Directory organization makes sense
- [ ] Migration process is acceptable
- [ ] Timeline is reasonable

**Once approved, I'll execute the refactoring systematically!**

