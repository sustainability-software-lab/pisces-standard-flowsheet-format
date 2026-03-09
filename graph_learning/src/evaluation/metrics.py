"""
Evaluation metrics for GNN predictions
"""

import numpy as np
from typing import Dict, List
import torch
from torch_geometric.data import Data


def calculate_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    """
    Calculate standard regression metrics.
    
    Args:
        y_true: True values
        y_pred: Predicted values
        
    Returns:
        Dictionary of metrics
    """
    # Mean Absolute Error
    mae = np.mean(np.abs(y_true - y_pred))
    
    # Mean Squared Error
    mse = np.mean((y_true - y_pred) ** 2)
    
    # Root Mean Squared Error
    rmse = np.sqrt(mse)
    
    # Mean Absolute Percentage Error
    mape = np.mean(np.abs((y_true - y_pred) / (y_true + 1e-8))) * 100
    
    # R² Score
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    
    return {
        'MAE': mae,
        'MSE': mse,
        'RMSE': rmse,
        'MAPE': mape,
        'R2': r2
    }


def calculate_weighted_error(
    predictions: np.ndarray,
    targets: np.ndarray,
    weights: np.ndarray,
    error_type: str = 'mae'
) -> float:
    """
    Calculate weighted error for predictions.
    
    Args:
        predictions: Predicted values
        targets: True values
        weights: Importance weights for each prediction
        error_type: Type of error ('mae', 'mse', 'rmse')
        
    Returns:
        Weighted error value
    """
    if error_type == 'mae':
        errors = np.abs(predictions - targets)
    elif error_type in ['mse', 'rmse']:
        errors = (predictions - targets) ** 2
    else:
        raise ValueError(f"Unknown error type: {error_type}")
    
    # Normalize weights
    weights_normalized = weights / np.sum(weights)
    
    # Calculate weighted error
    weighted_error = np.sum(errors * weights_normalized)
    
    if error_type == 'rmse':
        weighted_error = np.sqrt(weighted_error)
    
    return weighted_error


def calculate_node_level_metrics(
    model: torch.nn.Module,
    dataset: List[Data],
    device: str = 'cpu'
) -> Dict[str, np.ndarray]:
    """
    Calculate node-level predictions and errors for importance weighting.
    
    This is useful for understanding which parts of the process graph
    contribute most to prediction errors.
    
    Args:
        model: Trained GNN model
        dataset: List of graph Data objects
        device: Device to run on
        
    Returns:
        Dictionary with node-level statistics
    """
    model.eval()
    device = torch.device(device)
    
    node_embeddings = []
    node_costs = []
    
    with torch.no_grad():
        for data in dataset:
            data = data.to(device)
            
            # Get node embeddings from the model
            # (This assumes model has a method to extract embeddings)
            # For now, we'll use node features as a proxy
            node_embeddings.append(data.x.cpu().numpy())
            
            # Extract node costs (assuming it's in the features)
            # This would be the installed_cost or purchase_cost
            node_costs.append(data.x[:, 1].cpu().numpy())  # Assuming cost is feature 1
    
    return {
        'embeddings': node_embeddings,
        'costs': node_costs
    }

