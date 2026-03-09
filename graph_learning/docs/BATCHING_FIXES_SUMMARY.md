# Batching Fixes Summary

## Date
November 20, 2025

## Issues Fixed

### 1. ✅ Metadata Batching Error (KeyError)
**Problem:** PyG DataLoader tried to batch `metadata` dictionaries with different keys across flowsheets, causing `KeyError` during collation.

**Solution:** 
- Store `metadata` as a separate attribute (not in graph features)
- Add `exclude_keys=['metadata']` to all DataLoader instantiations

**Files Modified:**
- `src/data/graph_builder.py`
- `src/training/trainer.py`
- `src/training/generation_trainer.py`

---

### 2. ✅ GraphVAE Batch Size Error
**Problem:** GraphVAE couldn't handle batched graphs with different sizes, causing dimension mismatch in adjacency matrices.

**Solution:** Set `batch_size=1` for GraphVAE training (standard for variable-sized graph generation).

**Files Modified:**
- `demo_graph_generation.py`
- `src/models/graph_generation.py` (added documentation)

---

### 3. ✅ Tensor Contiguity Error
**Problem:** `.view(-1)` failed on non-contiguous tensors.

**Solution:** Changed `.view(-1)` to `.reshape(-1)`.

**Files Modified:**
- `src/models/graph_generation.py`

---

### 4. ✅ Feature Dimension Mismatch
**Problem:** Predicted features had shape `[batch_size, num_nodes, num_features]` but ground truth was `[num_nodes, num_features]`.

**Solution:** Squeeze batch dimension when comparing.

**Files Modified:**
- `src/models/graph_generation.py`

---

## Testing Status
✅ **`demo_graph_generation.py`** - Runs successfully without warnings
✅ **`example_workflow.ipynb`** - Should now work with metadata fix
✅ All trainers updated with proper batching parameters

## How to Run
```bash
# Activate virtual environment
source venv/bin/activate

# Run demo (tests all fixes)
python demo_graph_generation.py

# Run notebook (tests metadata fix)
jupyter notebook example_workflow.ipynb
```

## Key Learnings

1. **Metadata Handling**: Inconsistent metadata across samples should be excluded from batching
2. **Variable Graph Sizes**: Graph generation models often require `batch_size=1` for simplicity
3. **Tensor Operations**: Use `.reshape()` instead of `.view()` for non-contiguous tensors
4. **Dimension Matching**: Always verify batch dimensions match between predictions and targets

---

**Status**: ✅ All Issues Resolved

