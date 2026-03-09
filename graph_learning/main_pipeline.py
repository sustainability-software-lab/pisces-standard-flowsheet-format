"""
Main pipeline script to train and evaluate GNN models on chemical process flowsheets
"""

import os
import yaml
import logging
import argparse
from pathlib import Path
import torch
import numpy as np
import json

from src.data.data_loader import FlowsheetDataLoader
from src.data.feature_extractor import FeatureExtractor
from src.data.graph_builder import FlowsheetGraphBuilder
from src.models.process_gnn import ProcessGNN
from src.training.trainer import Trainer
from src.training.utils import train_test_split_graphs
from src.evaluation.weighted_scorer import WeightedScorer
from src.inference.predictor import Predictor
from src.inference.gemini_explainer import GeminiExplainer

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = 'config.yaml') -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def main(args):
    """Main training and evaluation pipeline"""
    
    # Load configuration
    config = load_config(args.config)
    logger.info("Loaded configuration")
    
    # Create output directories
    output_dir = Path(config['data']['output_dir'])
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # ========== Phase 1: Load and prepare data ==========
    logger.info("Phase 1: Loading and preparing data...")
    
    data_dir = config['data']['flowsheet_dir']
    loader = FlowsheetDataLoader(data_dir)
    flowsheets = loader.load_all_flowsheets()
    
    # Print statistics
    stats = loader.get_statistics()
    logger.info(f"Dataset statistics: {json.dumps(stats, indent=2)}")
    
    # ========== Phase 1: Extract features and build graphs ==========
    logger.info("Extracting features and building graphs...")
    
    feature_extractor = FeatureExtractor()
    feature_extractor.fit(flowsheets)
    
    graph_builder = FlowsheetGraphBuilder(feature_extractor)
    dataset = graph_builder.build_dataset(
        flowsheets,
        target_type=config['features']['target']
    )
    
    graph_stats = graph_builder.get_dataset_statistics(dataset)
    logger.info(f"Graph dataset statistics: {json.dumps(graph_stats, indent=2)}")
    
    # ========== Phase 1: Split data ==========
    train_ratio = config['training']['validation_split']
    test_ratio = config['training']['test_split']
    train_ratio = 1.0 - train_ratio - test_ratio
    
    train_dataset, val_dataset, test_dataset = train_test_split_graphs(
        dataset,
        train_ratio=train_ratio,
        val_ratio=config['training']['validation_split'],
        test_ratio=config['training']['test_split']
    )
    
    # ========== Phase 2: Build model ==========
    logger.info("Phase 2: Building GNN model...")
    
    num_node_features = dataset[0].num_node_features
    num_edge_features = dataset[0].num_edge_features if dataset[0].edge_attr is not None else 0
    
    model = ProcessGNN(
        num_node_features=num_node_features,
        num_edge_features=num_edge_features,
        hidden_channels=config['model']['hidden_channels'],
        num_layers=config['model']['num_gat_layers'],
        heads=config['model']['heads'],
        dropout=config['model']['dropout'],
        output_dim=config['model']['output_dim']
    )
    
    logger.info(f"Model architecture:\n{model}")
    logger.info(f"Total parameters: {sum(p.numel() for p in model.parameters())}")
    
    # ========== Phase 3: Train model ==========
    logger.info("Phase 3: Training model...")
    
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    logger.info(f"Using device: {device}")
    
    trainer = Trainer(
        model=model,
        train_dataset=train_dataset,
        val_dataset=val_dataset,
        batch_size=config['training']['batch_size'],
        learning_rate=config['training']['learning_rate'],
        device=device,
        checkpoint_dir=output_dir / 'checkpoints'
    )
    
    history = trainer.train(
        num_epochs=config['training']['num_epochs'],
        early_stopping_patience=config['training']['early_stopping_patience'],
        verbose=True
    )
    
    # Save training history
    with open(output_dir / 'training_history.json', 'w') as f:
        json.dump(history, f, indent=2)
    logger.info("Saved training history")
    
    # ========== Phase 4: Evaluate with weighted scoring ==========
    logger.info("Phase 4: Evaluating with weighted scoring...")
    
    scorer = WeightedScorer(weight_by=config['scoring']['weight_by'])
    
    # Evaluate on test set
    test_scores = scorer.calculate_weighted_score(model, test_dataset, device)
    logger.info(f"Test set scores: {json.dumps(test_scores, indent=2)}")
    
    # Get detailed breakdown
    detailed_results = scorer.evaluate_with_breakdown(model, test_dataset, device)
    
    # Save evaluation results
    with open(output_dir / 'evaluation_results.json', 'w') as f:
        json.dump(detailed_results, f, indent=2)
    logger.info("Saved evaluation results")
    
    # ========== Phase 5: Inference and LLM explanation ==========
    if args.use_gemini:
        logger.info("Phase 5: Generating explanations with Gemini...")
        
        try:
            predictor = Predictor(model, device=device)
            explainer = GeminiExplainer(
                project_id=config['gemini'].get('project_id'),
                location=config['gemini']['location'],
                model_name=config['gemini']['model_name']
            )
            
            # Generate explanation for first test sample
            test_sample = test_dataset[0]
            explanation_data = predictor.explain_prediction(test_sample)
            
            # Get natural language explanation
            nl_explanation = explainer.explain_prediction(
                explanation_data['prediction'],
                explanation_data,
                audience='technical'
            )
            
            logger.info(f"\n{'='*80}")
            logger.info("GEMINI EXPLANATION FOR SAMPLE PROCESS:")
            logger.info(f"{'='*80}")
            logger.info(nl_explanation)
            logger.info(f"{'='*80}\n")
            
            # Get improvement suggestions
            suggestions = explainer.suggest_improvements(
                explanation_data['prediction'],
                explanation_data
            )
            
            logger.info(f"\n{'='*80}")
            logger.info("IMPROVEMENT SUGGESTIONS:")
            logger.info(f"{'='*80}")
            logger.info(suggestions)
            logger.info(f"{'='*80}\n")
            
            # Save explanations
            with open(output_dir / 'sample_explanation.txt', 'w') as f:
                f.write("PREDICTION EXPLANATION:\n")
                f.write("=" * 80 + "\n")
                f.write(nl_explanation + "\n\n")
                f.write("IMPROVEMENT SUGGESTIONS:\n")
                f.write("=" * 80 + "\n")
                f.write(suggestions + "\n")
            
        except Exception as e:
            logger.warning(f"Could not generate Gemini explanations: {str(e)}")
            logger.info("To use Gemini, set up Google Cloud credentials and GOOGLE_CLOUD_PROJECT env variable")
    
    logger.info(f"\n{'='*80}")
    logger.info("Pipeline completed successfully!")
    logger.info(f"Results saved to: {output_dir}")
    logger.info(f"{'='*80}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train GNN model on chemical process flowsheets")
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file'
    )
    parser.add_argument(
        '--use-gemini',
        action='store_true',
        help='Use Gemini API for natural language explanations'
    )
    
    args = parser.parse_args()
    main(args)

