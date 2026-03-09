# Graph Generation Deep Dive Notebook - Fixes Applied

## Date
November 20, 2025

## Issues Fixed

### 1. ✅ Import Error: FlowsheetFeatureExtractor
**Problem:** Tried to import `FlowsheetFeatureExtractor` but the actual class name is `FeatureExtractor`.

**Error:**
```python
ImportError: cannot import name 'FlowsheetFeatureExtractor' from 'src.data.feature_extractor'
```

**Fix Applied:**
- **Cell 2** (Imports): Changed `from src.data.feature_extractor import FlowsheetFeatureExtractor` to `from src.data.feature_extractor import FeatureExtractor`
- **Cell 13** (Instantiation): Changed `feature_extractor = FlowsheetFeatureExtractor()` to `feature_extractor = FeatureExtractor()`

---

### 2. ✅ KFold Parameter Error
**Problem:** Used incorrect parameter name `random_seed` instead of `random_state` for sklearn's KFold.

**Potential Error:**
```python
TypeError: KFold() got an unexpected keyword argument 'random_seed'
```

**Fix Applied:**
- **Cell 18**: Changed `KFold(n_splits=n_splits, shuffle=True, random_seed=42)` to `KFold(n_splits=n_splits, shuffle=True, random_state=42)`

---

### 3. ✅ Wrong Trainer Reference
**Problem:** Used wrong variable name `trainer` instead of `trainer_exp2` when training experiment 2.

**Potential Error:**
```python
NameError: name 'trainer' is not defined
```

**Fix Applied:**
- **Cell 24**: Changed `history_exp2 = trainer.train(num_epochs=30, verbose=False)` to `history_exp2 = trainer_exp2.train(num_epochs=30, verbose=False)`

---

### 4. ✅ Import Error: GraphBuilder
**Problem:** Tried to import `GraphBuilder` but the actual class name is `FlowsheetGraphBuilder`.

**Error:**
```python
ImportError: cannot import name 'GraphBuilder' from 'src.data.graph_builder'
```

**Fix Applied:**
- **Cell 2** (Imports): Changed `from src.data.graph_builder import GraphBuilder` to `from src.data.graph_builder import FlowsheetGraphBuilder`
- **Cell 13** (Instantiation): Changed `graph_builder = GraphBuilder(feature_extractor)` to `graph_builder = FlowsheetGraphBuilder(feature_extractor)`

---

### 5. ✅ Config Key Error
**Problem:** Tried to access `config['data']['raw_flowsheets_dir']` but the actual key is `flowsheet_dir`.

**Error:**
```python
KeyError: 'raw_flowsheets_dir'
```

**Fix Applied:**
- **Cell 4** (Data Loading): Changed `data_path = config['data']['raw_flowsheets_dir']` to `data_path = config['data']['flowsheet_dir']`

---

### 6. ✅ Metadata Key Error
**Problem:** Tried to access `fs['metadata']['flowsheet_name']` but the actual key is `process_title`.

**Error:**
```python
KeyError: 'flowsheet_name'
```

**Fix Applied:**
- **Cell 4** (Data Loading): Changed to use `.get()` method with fallback: `fs.get('metadata', {}).get('process_title') or fs.get('metadata', {}).get('product_name', f'Flowsheet {i}')`
- Added truncation for long names (> 80 chars)

---

### 7. ✅ Data Structure Error: Units and Streams
**Problem:** Code assumed `units` and `streams` were dictionaries, but they are actually lists of dictionaries.

**Error:**
```python
AttributeError: 'list' object has no attribute 'items'
```

**Fix Applied:**
- **Cell 6** (Exploration): Changed from `.items()` iteration to list iteration, accessing `id` and `unit_type` fields from each dict
- **Cell 8** (Statistics): Changed from `fs['units'].values()` to `fs['units']` (list iteration), and `unit.get('type')` to `unit.get('unit_type')`
- **Cell 11** (Visualization): Updated `flowsheet_to_networkx()` function to iterate over lists instead of using `.items()`

---

### 8. ✅ GraphVAE Parameter Names
**Problem:** Used incorrect parameter names when initializing GraphVAE model.

**Error:**
```python
TypeError: GraphVAE.__init__() got an unexpected keyword argument 'node_features'. Did you mean 'num_node_features'?
```

**Fix Applied:**
- **Cell 16** (Model Init): Changed `node_features` → `num_node_features`, `edge_features` → `num_edge_features`, `hidden_dim` → `hidden_channels`
- **Cell 19** (Training Loop): Fixed GraphVAE instantiation with correct parameter names
- **Cell 24** (Experiment): Fixed model_exp2 instantiation with correct parameter names
- **Cell 26** (Generation): Fixed final_model instantiation with correct parameter names

**Correct Parameter Names:**
- `num_node_features` (not `node_features`)
- `num_edge_features` (not `edge_features`)
- `hidden_channels` (not `hidden_dim`)
- `latent_dim` ✅
- `max_num_nodes` ✅

---

### 9. ✅ Stream Field Names (SFF Format)
**Problem:** Code was looking for `from_unit` and `to_unit` fields in streams, but SFF format uses `source_unit_id` and `sink_unit_id`.

**Impact:** Resulted in 0 edges being loaded from real flowsheets, making visualizations show disconnected graphs.

**Fix Applied:**
- **Cell 6** (Data Exploration): Updated stream display to show `source_unit_id` and `sink_unit_id`
- **Cell 11** (NetworkX Conversion): Fixed `flowsheet_to_networkx()` function to use correct field names
- Added check to skip streams where source or sink is "None" (string)

**Correct SFF Field Names:**
- `source_unit_id` (not `from_unit`)
- `sink_unit_id` (not `to_unit`)

---

## Summary

All fixes have been applied to the notebook. The notebook should now run without import, configuration, metadata, data structure, model parameter, or SFF format errors.

**Total Fixes Applied: 9**

1. Import: `FlowsheetFeatureExtractor` → `FeatureExtractor`
2. Import: `GraphBuilder` → `FlowsheetGraphBuilder`
3. Parameter: `random_seed` → `random_state` (sklearn)
4. Variable: `trainer` → `trainer_exp2`
5. Config Key: `raw_flowsheets_dir` → `flowsheet_dir`
6. Metadata Key: `flowsheet_name` → `process_title` (with fallback)
7. Data Structure: Fixed `units` and `streams` from dict to list handling (3 cells)
8. Model Parameters: Fixed GraphVAE parameter names (4 cells affected)
9. **Stream Fields: `from_unit`/`to_unit` → `source_unit_id`/`sink_unit_id` (SFF format)**

### Files Modified:
- `graph_generation_deep_dive.ipynb` (13 cells updated, 9 total fixes)

### Testing Status:
✅ Import statements corrected (FeatureExtractor, FlowsheetGraphBuilder)  
✅ Class instantiation corrected  
✅ sklearn API usage corrected  
✅ Variable references corrected  

## How to Run

```bash
# Activate virtual environment
source venv/bin/activate

# Launch Jupyter
jupyter notebook graph_generation_deep_dive.ipynb

# Run cells sequentially
# All cells should now execute without errors
```

---

**Status**: ✅ All Issues Fixed - Notebook Ready to Use

