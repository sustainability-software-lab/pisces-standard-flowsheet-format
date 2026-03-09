"""
ProcessGNN: Graph Neural Network for chemical process prediction
"""

import torch
import torch.nn as nn
from torch.nn import Linear, Dropout, ReLU, BatchNorm1d
from torch_geometric.nn import GATConv, GATv2Conv, global_mean_pool, global_max_pool, global_add_pool
from typing import Optional


class ProcessGNN(nn.Module):
    """
    Graph Attention Network (GAT) for chemical process property prediction.
    
    This model uses graph attention layers to learn representations of chemical
    processes, with special attention to important connections (streams) between
    unit operations.
    """
    
    def __init__(
        self,
        num_node_features: int,
        num_edge_features: int,
        hidden_channels: int = 64,
        num_layers: int = 2,
        heads: int = 4,
        dropout: float = 0.2,
        output_dim: int = 1,
        use_gatv2: bool = True,
        pooling: str = 'mean'
    ):
        """
        Initialize the ProcessGNN model.
        
        Args:
            num_node_features: Number of input node features
            num_edge_features: Number of input edge features
            hidden_channels: Number of hidden channels
            num_layers: Number of GAT layers
            heads: Number of attention heads
            dropout: Dropout probability
            output_dim: Output dimension (1 for regression)
            use_gatv2: Whether to use GATv2Conv (improved version)
            pooling: Graph pooling method ('mean', 'max', 'add')
        """
        super(ProcessGNN, self).__init__()
        
        self.num_layers = num_layers
        self.dropout = dropout
        self.pooling = pooling
        
        # Choose GAT version
        GATLayer = GATv2Conv if use_gatv2 else GATConv
        
        # Input projection layer
        self.input_proj = Linear(num_node_features, hidden_channels)
        
        # GAT layers
        self.convs = nn.ModuleList()
        self.batch_norms = nn.ModuleList()
        
        for i in range(num_layers):
            if i == 0:
                # First layer
                conv = GATLayer(
                    hidden_channels,
                    hidden_channels,
                    heads=heads,
                    dropout=dropout,
                    edge_dim=num_edge_features if num_edge_features > 0 else None,
                    concat=True
                )
            else:
                # Subsequent layers
                conv = GATLayer(
                    hidden_channels * heads,
                    hidden_channels,
                    heads=heads,
                    dropout=dropout,
                    edge_dim=num_edge_features if num_edge_features > 0 else None,
                    concat=True if i < num_layers - 1 else False
                )
            
            self.convs.append(conv)
            
            # Batch normalization
            out_channels = hidden_channels * heads if i < num_layers - 1 else hidden_channels
            self.batch_norms.append(BatchNorm1d(out_channels))
        
        # Readout MLP
        self.mlp = nn.Sequential(
            Linear(hidden_channels, hidden_channels // 2),
            ReLU(),
            Dropout(dropout),
            Linear(hidden_channels // 2, hidden_channels // 4),
            ReLU(),
            Dropout(dropout),
            Linear(hidden_channels // 4, output_dim)
        )
    
    def forward(self, data):
        """
        Forward pass.
        
        Args:
            data: PyTorch Geometric Data object with attributes:
                - x: Node features [num_nodes, num_node_features]
                - edge_index: Edge connectivity [2, num_edges]
                - edge_attr: Edge features [num_edges, num_edge_features] (optional)
                - batch: Batch vector [num_nodes] (for batched graphs)
        
        Returns:
            Predictions [batch_size, output_dim]
        """
        x, edge_index, batch = data.x, data.edge_index, data.batch
        edge_attr = data.edge_attr if hasattr(data, 'edge_attr') else None
        
        # Input projection
        x = self.input_proj(x)
        x = ReLU()(x)
        
        # Apply GAT layers
        for i, (conv, bn) in enumerate(zip(self.convs, self.batch_norms)):
            x = conv(x, edge_index, edge_attr=edge_attr)
            x = bn(x)
            
            if i < self.num_layers - 1:
                x = ReLU()(x)
                x = Dropout(self.dropout)(x)
        
        # Graph-level readout (pooling)
        if self.pooling == 'mean':
            x = global_mean_pool(x, batch)
        elif self.pooling == 'max':
            x = global_max_pool(x, batch)
        elif self.pooling == 'add':
            x = global_add_pool(x, batch)
        else:
            raise ValueError(f"Unknown pooling method: {self.pooling}")
        
        # Final MLP for prediction
        out = self.mlp(x)
        
        return out
    
    def get_attention_weights(self, data):
        """
        Get attention weights from the first GAT layer for visualization.
        
        Args:
            data: PyTorch Geometric Data object
            
        Returns:
            Attention weights for each edge
        """
        x, edge_index = data.x, data.edge_index
        edge_attr = data.edge_attr if hasattr(data, 'edge_attr') else None
        
        # Input projection
        x = self.input_proj(x)
        x = ReLU()(x)
        
        # Get attention from first layer
        x, (edge_index, attention_weights) = self.convs[0](
            x, edge_index, edge_attr=edge_attr, return_attention_weights=True
        )
        
        return attention_weights


class EnsembleProcessGNN(nn.Module):
    """
    Ensemble of ProcessGNN models for improved predictions and uncertainty estimation.
    """
    
    def __init__(self, num_models: int, **model_kwargs):
        """
        Initialize ensemble.
        
        Args:
            num_models: Number of models in ensemble
            **model_kwargs: Arguments passed to each ProcessGNN
        """
        super(EnsembleProcessGNN, self).__init__()
        
        self.num_models = num_models
        self.models = nn.ModuleList([
            ProcessGNN(**model_kwargs) for _ in range(num_models)
        ])
    
    def forward(self, data):
        """
        Forward pass through ensemble.
        
        Args:
            data: PyTorch Geometric Data object
            
        Returns:
            Mean prediction and standard deviation across ensemble
        """
        predictions = torch.stack([model(data) for model in self.models])
        
        mean_pred = predictions.mean(dim=0)
        std_pred = predictions.std(dim=0)
        
        return mean_pred, std_pred
    
    def predict_with_uncertainty(self, data):
        """
        Make predictions with uncertainty estimates.
        
        Args:
            data: PyTorch Geometric Data object
            
        Returns:
            Dictionary with mean, std, and confidence intervals
        """
        mean_pred, std_pred = self.forward(data)
        
        # 95% confidence interval (assuming normal distribution)
        ci_lower = mean_pred - 1.96 * std_pred
        ci_upper = mean_pred + 1.96 * std_pred
        
        return {
            'mean': mean_pred,
            'std': std_pred,
            'ci_lower': ci_lower,
            'ci_upper': ci_upper
        }

