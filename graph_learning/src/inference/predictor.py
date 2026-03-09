"""
Predictor: Make predictions with trained GNN models
"""

import torch
import numpy as np
from torch_geometric.data import Data
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


class Predictor:
    """Make predictions using trained ProcessGNN models"""
    
    def __init__(
        self,
        model: torch.nn.Module,
        device: str = 'cpu',
        denormalize_fn: Optional[callable] = None
    ):
        """
        Initialize predictor.
        
        Args:
            model: Trained ProcessGNN model
            device: Device to run inference on
            denormalize_fn: Optional function to denormalize predictions
        """
        self.model = model
        self.device = torch.device(device)
        self.model.to(self.device)
        self.model.eval()
        self.denormalize_fn = denormalize_fn
    
    @torch.no_grad()
    def predict(self, data: Data) -> Dict[str, Any]:
        """
        Make a prediction for a single process graph.
        
        Args:
            data: PyTorch Geometric Data object
            
        Returns:
            Dictionary with prediction and metadata
        """
        data = data.to(self.device)
        
        # Get prediction
        prediction = self.model(data).cpu().numpy().flatten()[0]
        
        # Denormalize if function provided
        if self.denormalize_fn:
            prediction = self.denormalize_fn(prediction)
        
        # Get attention weights if model supports it
        attention_weights = None
        if hasattr(self.model, 'get_attention_weights'):
            try:
                attention_weights = self.model.get_attention_weights(data)
            except Exception as e:
                logger.warning(f"Could not get attention weights: {str(e)}")
        
        result = {
            'prediction': float(prediction),
            'num_nodes': data.num_nodes,
            'num_edges': data.num_edges,
            'attention_weights': attention_weights,
        }
        
        # Add metadata if available
        if hasattr(data, 'metadata'):
            result['metadata'] = data.metadata
        
        # Add ground truth if available
        if hasattr(data, 'y'):
            target = data.y.cpu().numpy().flatten()[0]
            if self.denormalize_fn:
                target = self.denormalize_fn(target)
            result['ground_truth'] = float(target)
            result['absolute_error'] = abs(prediction - target)
            result['relative_error'] = abs(prediction - target) / (target + 1e-8) * 100
        
        return result
    
    @torch.no_grad()
    def predict_batch(self, dataset: List[Data]) -> List[Dict[str, Any]]:
        """
        Make predictions for multiple process graphs.
        
        Args:
            dataset: List of Data objects
            
        Returns:
            List of prediction dictionaries
        """
        results = []
        
        for data in dataset:
            result = self.predict(data)
            results.append(result)
        
        return results
    
    def get_important_streams(
        self,
        data: Data,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Identify the most important streams (edges) based on attention weights.
        
        Args:
            data: PyTorch Geometric Data object
            top_k: Number of top streams to return
            
        Returns:
            List of important stream information
        """
        if not hasattr(self.model, 'get_attention_weights'):
            logger.warning("Model does not support attention weight extraction")
            return []
        
        data = data.to(self.device)
        
        try:
            edge_index, attention_weights = self.model.get_attention_weights(data)
            
            # Average attention across heads if multiple heads
            if len(attention_weights.shape) > 1:
                attention_weights = attention_weights.mean(dim=1)
            
            attention_weights = attention_weights.cpu().numpy().flatten()
            
            # Get top-k edges
            top_k_indices = np.argsort(attention_weights)[-top_k:][::-1]
            
            important_streams = []
            for idx in top_k_indices:
                stream_info = {
                    'edge_index': int(idx),
                    'source_node': int(edge_index[0][idx]),
                    'target_node': int(edge_index[1][idx]),
                    'attention_weight': float(attention_weights[idx]),
                }
                
                # Add edge features if available
                if hasattr(data, 'edge_attr') and data.edge_attr is not None:
                    stream_info['features'] = data.edge_attr[idx].cpu().numpy().tolist()
                
                important_streams.append(stream_info)
            
            return important_streams
        
        except Exception as e:
            logger.error(f"Error getting important streams: {str(e)}")
            return []
    
    def explain_prediction(
        self,
        data: Data,
        top_k_streams: int = 5
    ) -> Dict[str, Any]:
        """
        Generate a comprehensive explanation for a prediction.
        
        Args:
            data: PyTorch Geometric Data object
            top_k_streams: Number of important streams to identify
            
        Returns:
            Dictionary with prediction and explanation components
        """
        # Get prediction
        prediction_result = self.predict(data)
        
        # Get important streams
        important_streams = self.get_important_streams(data, top_k=top_k_streams)
        
        # Get node statistics
        node_features = data.x.cpu().numpy()
        
        # Identify high-cost nodes (assuming cost is feature index 1)
        node_costs = node_features[:, 1]
        top_cost_nodes = np.argsort(node_costs)[-5:][::-1]
        
        explanation = {
            'prediction': prediction_result,
            'important_streams': important_streams,
            'high_cost_nodes': [
                {
                    'node_index': int(idx),
                    'cost': float(node_costs[idx]),
                    'features': node_features[idx].tolist()
                }
                for idx in top_cost_nodes
            ],
            'process_complexity': {
                'num_units': data.num_nodes,
                'num_streams': data.num_edges,
                'avg_degree': 2 * data.num_edges / data.num_nodes if data.num_nodes > 0 else 0
            }
        }
        
        return explanation

