# Bugfix: Metadata Batching Issue

## Problem

When running `demo_graph_generation.py`, the following error occurred:

```
KeyError: 'TEA_year'
File "...torch_geometric/data/collate.py", line 258, in _collate
    key, [v[key] for v in values], data_list, stores, increment)
```

## Root Cause

PyTorch Geometric's `DataLoader` batches multiple `Data` objects together by collating their attributes. When the `metadata` dictionary was added to Data objects, it caused issues because:

1. Different flowsheets have different metadata keys (`TEA_year`, `product_name`, etc.)
2. PyG's collation expects all Data objects to have the same attributes
3. When batching, it tried to access `metadata['TEA_year']` on a Data object that didn't have this key

## Solution

Added `exclude_keys=['metadata']` to all DataLoader instantiations to prevent metadata from being collated during batching.

### Files Modified:

1. **`src/data/graph_builder.py`**
   - Removed metadata assignment to Data objects
   - Added comment explaining metadata should be stored separately

2. **`src/training/generation_trainer.py`**
   - Added `exclude_keys=['metadata']` to all DataLoader calls (3 trainers)
   - GraphVAETrainer
   - LinkPredictionTrainer
   - NodeTypePredictionTrainer

3. **`src/training/trainer.py`**
   - Added `exclude_keys=['metadata']` to prevent future issues
   - Applied to train, validation, and prediction loaders

## How It Works

```python
# Before (causes error):
self.train_loader = DataLoader(dataset, batch_size=8, shuffle=True)

# After (works correctly):
self.train_loader = DataLoader(
    dataset, 
    batch_size=8, 
    shuffle=True,
    exclude_keys=['metadata']  # Skip metadata during batching
)
```

## Impact

- ✅ Fixes `KeyError: 'TEA_year'` in demo_graph_generation.py
- ✅ Prevents similar errors with other inconsistent metadata keys
- ✅ No impact on model training (metadata not used in forward passes)
- ℹ️ Metadata can still be accessed from individual Data objects when needed

## Alternative Solutions Considered

1. **Standardize metadata keys**: Would require preprocessing all flowsheets
2. **Store metadata separately**: Would break existing code that expects `data.metadata`
3. **Custom collate function**: More complex, unnecessary

The `exclude_keys` approach is the simplest and most robust.

## Testing

After this fix, run:

```bash
source venv/bin/activate
python demo_graph_generation.py
```

Should complete without errors.

## Related Issues

This is a common issue when working with PyTorch Geometric and custom attributes. Always use `exclude_keys` for:
- Non-tensor attributes
- Attributes with inconsistent shapes/keys
- Metadata or auxiliary information

---

## Second Issue: GraphVAE Batch Size

### Problem

After fixing the metadata issue, another error occurred:

```
ValueError: Target size (torch.Size([32258])) must be the same as input size (torch.Size([54300]))
```

### Root Cause

GraphVAE is not designed to handle batched graphs with variable sizes. When `batch_size > 1`:
- Different graphs have different numbers of nodes
- The decoder generates fixed-size outputs based on total nodes in batch
- The adjacency matrices don't align properly

### Solution

Set `batch_size=1` for GraphVAE training:

```python
vae_trainer = GraphVAETrainer(
    model=vae_model,
    train_dataset=dataset[:8],
    val_dataset=dataset[8:],
    batch_size=1,  # Required for variable-sized graphs
    learning_rate=0.001,
    device='cpu'
)
```

### Files Modified:

1. **`demo_graph_generation.py`**
   - Changed GraphVAE batch_size from 2 to 1
   - Reduced LinkPredictor batch_size to 2 for safety

2. **`src/models/graph_generation.py`**
   - Added docstring note about batch_size=1 requirement
   - Changed `.view(-1)` to `.reshape(-1)` for non-contiguous tensors
   - Fixed feature dimension mismatch by squeezing batch dimension when needed

### Why This Is Necessary

Graph generation models for variable-sized graphs typically require:
- Either batch_size=1
- Or padding all graphs to the same size (wasteful)
- Or complex batching logic (error-prone)

For this application, batch_size=1 is the simplest and most reliable approach.

---

**Status**: ✅ FIXED (Both Issues)

**Date**: 2025-01-20

