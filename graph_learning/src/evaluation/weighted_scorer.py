"""
WeightedScorer: Evaluate models using weighted scoring based on component importance
"""

import numpy as np
import torch
from torch_geometric.data import Data
from typing import List, Dict, Any, Callable
import logging

from .metrics import calculate_metrics, calculate_weighted_error

logger = logging.getLogger(__name__)


class WeightedScorer:
    """
    Evaluates GNN predictions using weighted scoring based on component importance.
    
    This is particularly useful for chemical process evaluation where errors in
    expensive or critical equipment should be penalized more heavily.
    """
    
    def __init__(
        self,
        weight_by: str = 'installed_cost',
        weight_function: Callable = None
    ):
        """
        Initialize weighted scorer.
        
        Args:
            weight_by: How to calculate weights ('installed_cost', 'power_consumption', 
                      'heat_duty', or 'custom')
            weight_function: Custom function to calculate weights from Data object
        """
        self.weight_by = weight_by
        self.weight_function = weight_function
        
        if weight_by == 'custom' and weight_function is None:
            raise ValueError("Must provide weight_function when weight_by='custom'")
    
    def get_importance_weights(self, data: Data) -> np.ndarray:
        """
        Calculate importance weights for nodes in a graph.
        
        Args:
            data: PyTorch Geometric Data object
            
        Returns:
            Array of weights (one per node)
        """
        if self.weight_by == 'custom':
            return self.weight_function(data)
        
        # Extract features
        node_features = data.x.cpu().numpy()
        
        if self.weight_by == 'installed_cost':
            # Assuming installed cost is feature index 1 (after unit type encoding)
            weights = node_features[:, 1]
        
        elif self.weight_by == 'power_consumption':
            # Assuming power consumption is feature index 3
            weights = node_features[:, 3]
        
        elif self.weight_by == 'heat_duty':
            # Assuming heat duty is feature index 4
            weights = node_features[:, 4]
        
        else:
            raise ValueError(f"Unknown weight_by method: {self.weight_by}")
        
        # Ensure non-negative weights
        weights = np.abs(weights)
        
        # Normalize to sum to 1
        if np.sum(weights) > 0:
            weights = weights / np.sum(weights)
        else:
            # If all weights are zero, use uniform weights
            weights = np.ones_like(weights) / len(weights)
        
        return weights
    
    def calculate_weighted_score(
        self,
        model: torch.nn.Module,
        dataset: List[Data],
        device: str = 'cpu'
    ) -> Dict[str, float]:
        """
        Calculate weighted error score for a model on a dataset.
        
        Args:
            model: Trained GNN model
            dataset: List of Data objects
            device: Device to run on
            
        Returns:
            Dictionary with weighted and unweighted scores
        """
        model.eval()
        device = torch.device(device)
        
        all_predictions = []
        all_targets = []
        all_weights = []
        
        with torch.no_grad():
            for data in dataset:
                data = data.to(device)
                
                # Make prediction
                pred = model(data).cpu().numpy().flatten()
                target = data.y.cpu().numpy().flatten()
                
                # Get importance weights for this graph
                weights = self.get_importance_weights(data)
                
                # For graph-level prediction, aggregate node weights
                # Here we use the sum of weights as the graph importance
                graph_weight = np.sum(weights)
                
                all_predictions.extend(pred)
                all_targets.extend(target)
                all_weights.append(graph_weight)
        
        all_predictions = np.array(all_predictions)
        all_targets = np.array(all_targets)
        all_weights = np.array(all_weights)
        
        # Calculate standard metrics
        standard_metrics = calculate_metrics(all_targets, all_predictions)
        
        # Calculate weighted errors
        weighted_mae = calculate_weighted_error(
            all_predictions, all_targets, all_weights, error_type='mae'
        )
        weighted_mse = calculate_weighted_error(
            all_predictions, all_targets, all_weights, error_type='mse'
        )
        weighted_rmse = calculate_weighted_error(
            all_predictions, all_targets, all_weights, error_type='rmse'
        )
        
        # Combine results
        results = {
            **standard_metrics,
            'Weighted_MAE': weighted_mae,
            'Weighted_MSE': weighted_mse,
            'Weighted_RMSE': weighted_rmse,
        }
        
        return results
    
    def evaluate_with_breakdown(
        self,
        model: torch.nn.Module,
        dataset: List[Data],
        device: str = 'cpu'
    ) -> Dict[str, Any]:
        """
        Evaluate model with detailed breakdown by process.
        
        Args:
            model: Trained GNN model
            dataset: List of Data objects
            device: Device to run on
            
        Returns:
            Dictionary with overall scores and per-process breakdown
        """
        model.eval()
        device = torch.device(device)
        
        overall_scores = self.calculate_weighted_score(model, dataset, device)
        
        # Per-process breakdown
        process_results = []
        
        with torch.no_grad():
            for i, data in enumerate(dataset):
                data = data.to(device)
                
                # Make prediction
                pred = model(data).cpu().numpy().flatten()[0]
                target = data.y.cpu().numpy().flatten()[0]
                
                # Calculate error
                error = abs(pred - target)
                relative_error = error / (target + 1e-8) * 100
                
                # Get importance weights
                weights = self.get_importance_weights(data)
                total_importance = np.sum(weights)
                
                process_info = {
                    'process_id': i,
                    'prediction': pred,
                    'target': target,
                    'absolute_error': error,
                    'relative_error': relative_error,
                    'importance_weight': total_importance,
                    'num_nodes': data.num_nodes,
                    'num_edges': data.num_edges,
                }
                
                # Add metadata if available
                if hasattr(data, 'metadata'):
                    process_info['metadata'] = data.metadata
                
                process_results.append(process_info)
        
        return {
            'overall_scores': overall_scores,
            'process_breakdown': process_results
        }
    
    def identify_high_error_components(
        self,
        model: torch.nn.Module,
        data: Data,
        device: str = 'cpu',
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Identify which components (nodes) contribute most to prediction error.
        
        This is useful for understanding model weaknesses and focusing improvements.
        
        Args:
            model: Trained GNN model
            data: Single Data object
            device: Device to run on
            top_k: Number of top error-contributing nodes to return
            
        Returns:
            List of dictionaries with node information
        """
        model.eval()
        device = torch.device(device)
        data = data.to(device)
        
        # Get node-level embeddings and predictions
        # This would require modifying the model to return intermediate representations
        # For now, we'll identify high-cost nodes as proxies for important components
        
        weights = self.get_importance_weights(data)
        node_features = data.x.cpu().numpy()
        
        # Find top-k important nodes
        top_k_indices = np.argsort(weights)[-top_k:][::-1]
        
        high_importance_nodes = []
        for idx in top_k_indices:
            node_info = {
                'node_index': int(idx),
                'importance_weight': float(weights[idx]),
                'features': node_features[idx].tolist(),
            }
            high_importance_nodes.append(node_info)
        
        return high_importance_nodes

