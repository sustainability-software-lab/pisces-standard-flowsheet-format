#!/usr/bin/env python
"""
Quick demonstration of the GNN-based chemical process analysis workflow.

This script provides a minimal example of the complete pipeline:
1. Load flowsheet data
2. Build graph dataset
3. Train a GNN model
4. Evaluate predictions
5. Generate explanations

Usage:
    python quick_demo.py
"""

import sys
import torch
import numpy as np

# Import our modules
from src.data.data_loader import FlowsheetDataLoader
from src.data.feature_extractor import FeatureExtractor
from src.data.graph_builder import FlowsheetGraphBuilder
from src.models.process_gnn import ProcessGNN
from src.training.trainer import Trainer
from src.training.utils import train_test_split_graphs
from src.evaluation.weighted_scorer import WeightedScorer
from src.inference.predictor import Predictor

def main():
    print("="*80)
    print("GNN-Based Chemical Process Analysis - Quick Demo")
    print("="*80)
    
    # ========== Phase 1: Load Data ==========
    print("\n[1/5] Loading flowsheet data...")
    loader = FlowsheetDataLoader('../exported_flowsheets/bioindustrial_park')
    flowsheets = loader.load_all_flowsheets()
    print(f"    ✓ Loaded {len(flowsheets)} flowsheets")
    
    stats = loader.get_statistics()
    print(f"    ✓ Average units per flowsheet: {stats['avg_num_units']:.1f}")
    print(f"    ✓ Average streams per flowsheet: {stats['avg_num_streams']:.1f}")
    
    # ========== Phase 2: Build Graphs ==========
    print("\n[2/5] Building graph representations...")
    feature_extractor = FeatureExtractor()
    feature_extractor.fit(flowsheets)
    
    graph_builder = FlowsheetGraphBuilder(feature_extractor)
    dataset = graph_builder.build_dataset(flowsheets, target_type='total_installed_cost')
    print(f"    ✓ Built {len(dataset)} graphs")
    print(f"    ✓ Node features: {dataset[0].num_node_features}")
    print(f"    ✓ Edge features: {dataset[0].num_edge_features if dataset[0].edge_attr is not None else 0}")
    
    # Split data
    train_dataset, val_dataset, test_dataset = train_test_split_graphs(
        dataset, train_ratio=0.7, val_ratio=0.15, test_ratio=0.15, seed=42
    )
    print(f"    ✓ Split: {len(train_dataset)} train, {len(val_dataset)} val, {len(test_dataset)} test")
    
    # ========== Phase 3: Build and Train Model ==========
    print("\n[3/5] Training Graph Neural Network...")
    
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"    ✓ Using device: {device}")
    
    model = ProcessGNN(
        num_node_features=dataset[0].num_node_features,
        num_edge_features=dataset[0].num_edge_features if dataset[0].edge_attr is not None else 0,
        hidden_channels=64,
        num_layers=2,
        heads=4,
        dropout=0.2
    )
    print(f"    ✓ Model parameters: {sum(p.numel() for p in model.parameters()):,}")
    
    trainer = Trainer(
        model=model,
        train_dataset=train_dataset,
        val_dataset=val_dataset,
        batch_size=8,
        learning_rate=0.001,
        device=device
    )
    
    print("    ✓ Training (this may take a few minutes)...")
    history = trainer.train(num_epochs=100, early_stopping_patience=20, verbose=False)
    
    final_val_loss = history['val_loss'][-1]
    final_val_r2 = history['val_r2'][-1]
    print(f"    ✓ Training complete!")
    print(f"    ✓ Final validation loss: {final_val_loss:,.0f}")
    print(f"    ✓ Final validation R²: {final_val_r2:.4f}")
    
    # ========== Phase 4: Evaluate ==========
    print("\n[4/5] Evaluating on test set...")
    
    scorer = WeightedScorer(weight_by='installed_cost')
    test_scores = scorer.calculate_weighted_score(model, test_dataset, device)
    
    print("    ✓ Test Set Metrics:")
    for metric in ['MAE', 'RMSE', 'R2', 'Weighted_MAE']:
        if metric in test_scores:
            value = test_scores[metric]
            if metric == 'R2':
                print(f"        {metric:20s}: {value:.4f}")
            else:
                print(f"        {metric:20s}: ${value:,.0f}")
    
    # ========== Phase 5: Make Predictions ==========
    print("\n[5/5] Generating predictions and explanations...")
    
    predictor = Predictor(model, device=device)
    
    # Predict on all test samples
    predictions = trainer.predict(test_dataset)
    targets = np.array([data.y.item() for data in test_dataset])
    
    print(f"    ✓ Made {len(predictions)} predictions")
    print(f"\n    Sample Predictions:")
    print(f"    {'Actual':>15s}  {'Predicted':>15s}  {'Error':>10s}")
    print(f"    {'-'*15}  {'-'*15}  {'-'*10}")
    
    for i in range(min(3, len(test_dataset))):
        actual = targets[i]
        pred = predictions[i][0]
        error = abs(pred - actual) / actual * 100
        print(f"    ${actual:>14,.0f}  ${pred:>14,.0f}  {error:>9.2f}%")
    
    # Detailed explanation for first test sample
    if len(test_dataset) > 0:
        print(f"\n    Detailed Explanation for Sample Process:")
        explanation = predictor.explain_prediction(test_dataset[0], top_k_streams=3)
        
        pred_info = explanation['prediction']
        print(f"        Predicted Cost: ${pred_info['prediction']:,.0f}")
        if 'ground_truth' in pred_info:
            print(f"        Actual Cost: ${pred_info['ground_truth']:,.0f}")
            print(f"        Relative Error: {pred_info['relative_error']:.2f}%")
        
        print(f"\n        Process Complexity:")
        print(f"            Units: {explanation['process_complexity']['num_units']}")
        print(f"            Streams: {explanation['process_complexity']['num_streams']}")
        
        if explanation['important_streams']:
            print(f"\n        Most Important Streams (by attention):")
            for i, stream in enumerate(explanation['important_streams'][:3], 1):
                print(f"            {i}. Unit {stream['source_node']} → Unit {stream['target_node']} "
                      f"(weight: {stream['attention_weight']:.4f})")
    
    # ========== Summary ==========
    print("\n" + "="*80)
    print("Demo Complete! ✓")
    print("="*80)
    print("\nNext Steps:")
    print("  • View training history: history variable")
    print("  • Explore the notebook: jupyter notebook example_workflow.ipynb")
    print("  • Run full pipeline: python main_pipeline.py")
    print("  • Read documentation: GNN_PROJECT_README.md")
    print("\nFor LLM explanations, set up Gemini API and run:")
    print("  python main_pipeline.py --use-gemini")
    print("="*80)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nDemo interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nError: {str(e)}")
        print("\nFor troubleshooting, see QUICK_START_GUIDE.md")
        sys.exit(1)

