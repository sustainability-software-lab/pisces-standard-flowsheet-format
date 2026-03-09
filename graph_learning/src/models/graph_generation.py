"""
Graph Generation Models for Chemical Process Flowsheet Design

This module implements models for generating and predicting the structure of
chemical process flowsheets, including:
- Graph Variational Autoencoder (GraphVAE) for flowsheet generation
- Link prediction for stream connectivity
- Node type prediction for unit operations
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GATConv, GCNConv, global_mean_pool, global_add_pool
from torch_geometric.utils import negative_sampling, remove_self_loops, add_self_loops
from typing import Tuple, Optional
import numpy as np


class GraphVAE(nn.Module):
    """
    Graph Variational Autoencoder for generating chemical process flowsheets.
    
    Learns a latent representation of flowsheet structures and can generate
    new flowsheets by sampling from the latent space.
    
    Note: Due to variable graph sizes, this model requires batch_size=1 during training.
    Use batch_size=1 in the DataLoader to avoid dimension mismatch errors.
    """
    
    def __init__(
        self,
        num_node_features: int,
        num_edge_features: int,
        hidden_channels: int = 64,
        latent_dim: int = 32,
        max_num_nodes: int = 200
    ):
        """
        Initialize GraphVAE.
        
        Args:
            num_node_features: Number of input node features
            num_edge_features: Number of input edge features
            hidden_channels: Hidden dimension size
            latent_dim: Latent space dimension
            max_num_nodes: Maximum number of nodes in a graph
        """
        super(GraphVAE, self).__init__()
        
        self.latent_dim = latent_dim
        self.max_num_nodes = max_num_nodes
        self.num_node_features = num_node_features
        
        # Encoder
        self.encoder_conv1 = GATConv(num_node_features, hidden_channels, 
                                     edge_dim=num_edge_features if num_edge_features > 0 else None)
        self.encoder_conv2 = GATConv(hidden_channels, hidden_channels,
                                     edge_dim=num_edge_features if num_edge_features > 0 else None)
        
        # Latent space projection
        self.fc_mu = nn.Linear(hidden_channels, latent_dim)
        self.fc_logvar = nn.Linear(hidden_channels, latent_dim)
        
        # Decoder
        self.decoder_fc = nn.Linear(latent_dim, hidden_channels)
        
        # Adjacency matrix decoder
        self.adj_decoder = nn.Sequential(
            nn.Linear(hidden_channels, hidden_channels),
            nn.ReLU(),
            nn.Linear(hidden_channels, max_num_nodes)
        )
        
        # Node feature decoder
        self.node_decoder = nn.Sequential(
            nn.Linear(hidden_channels, hidden_channels),
            nn.ReLU(),
            nn.Linear(hidden_channels, num_node_features)
        )
    
    def encode(self, x, edge_index, edge_attr=None, batch=None):
        """Encode graph to latent distribution."""
        # Graph convolutions
        h = F.relu(self.encoder_conv1(x, edge_index, edge_attr))
        h = F.relu(self.encoder_conv2(h, edge_index, edge_attr))
        
        # Pool to graph-level representation
        if batch is None:
            batch = torch.zeros(x.size(0), dtype=torch.long, device=x.device)
        h = global_mean_pool(h, batch)
        
        # Get latent parameters
        mu = self.fc_mu(h)
        logvar = self.fc_logvar(h)
        
        return mu, logvar
    
    def reparameterize(self, mu, logvar):
        """Reparameterization trick."""
        std = torch.exp(0.5 * logvar)
        eps = torch.randn_like(std)
        return mu + eps * std
    
    def decode(self, z, num_nodes):
        """Decode latent vector to graph."""
        # Expand latent vector
        h = F.relu(self.decoder_fc(z))
        
        # Repeat for each node
        h_nodes = h.unsqueeze(1).repeat(1, num_nodes, 1)
        
        # Decode adjacency matrix
        adj_logits = self.adj_decoder(h_nodes)  # [batch_size, num_nodes, max_num_nodes]
        adj_logits = adj_logits[:, :, :num_nodes]  # Trim to actual size
        
        # Decode node features
        node_features = self.node_decoder(h_nodes)  # [batch_size, num_nodes, num_features]
        
        return adj_logits, node_features
    
    def forward(self, data):
        """Full forward pass."""
        x, edge_index = data.x, data.edge_index
        edge_attr = data.edge_attr if hasattr(data, 'edge_attr') else None
        batch = data.batch if hasattr(data, 'batch') else None
        
        # Encode
        mu, logvar = self.encode(x, edge_index, edge_attr, batch)
        
        # Reparameterize
        z = self.reparameterize(mu, logvar)
        
        # Decode
        num_nodes = data.num_nodes
        adj_logits, node_features = self.decode(z, num_nodes)
        
        return adj_logits, node_features, mu, logvar
    
    def loss_function(self, adj_logits, node_features, mu, logvar, true_adj, true_features):
        """
        VAE loss = Reconstruction loss + KL divergence.
        
        Args:
            adj_logits: Predicted adjacency matrix logits
            node_features: Predicted node features
            mu: Mean of latent distribution
            logvar: Log variance of latent distribution
            true_adj: Ground truth adjacency matrix
            true_features: Ground truth node features
        """
        # Adjacency reconstruction loss (binary cross-entropy)
        adj_recon_loss = F.binary_cross_entropy_with_logits(
            adj_logits.reshape(-1), true_adj.reshape(-1)
        )
        
        # Node feature reconstruction loss
        # node_features is [batch_size, num_nodes, num_features]
        # true_features is [num_nodes, num_features]
        # Need to match dimensions
        if node_features.dim() == 3 and true_features.dim() == 2:
            # Squeeze batch dimension if batch_size=1
            node_features = node_features.squeeze(0)
        feature_recon_loss = F.mse_loss(node_features, true_features)
        
        # KL divergence
        kld = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
        
        return adj_recon_loss + feature_recon_loss + 0.001 * kld
    
    def generate(self, num_graphs: int = 1, num_nodes: int = 50, device='cpu'):
        """
        Generate new flowsheet graphs.
        
        Args:
            num_graphs: Number of graphs to generate
            num_nodes: Number of nodes per graph
            device: Device to generate on
            
        Returns:
            Generated adjacency matrices and node features
        """
        self.eval()
        with torch.no_grad():
            # Sample from latent space
            z = torch.randn(num_graphs, self.latent_dim).to(device)
            
            # Decode
            adj_logits, node_features = self.decode(z, num_nodes)
            
            # Apply sigmoid to get probabilities
            adj_probs = torch.sigmoid(adj_logits)
            
            return adj_probs, node_features


class LinkPredictionGNN(nn.Module):
    """
    GNN for predicting links (streams) between units in a flowsheet.
    
    Given a partial flowsheet, predicts which streams should exist between units.
    """
    
    def __init__(
        self,
        num_node_features: int,
        num_edge_features: int,
        hidden_channels: int = 64,
        num_layers: int = 2
    ):
        """
        Initialize Link Prediction GNN.
        
        Args:
            num_node_features: Number of node features
            num_edge_features: Number of edge features
            hidden_channels: Hidden dimension
            num_layers: Number of GNN layers
        """
        super(LinkPredictionGNN, self).__init__()
        
        self.num_layers = num_layers
        
        # Node encoder
        self.node_encoder = nn.Linear(num_node_features, hidden_channels)
        
        # GNN layers
        self.convs = nn.ModuleList()
        for _ in range(num_layers):
            self.convs.append(
                GATConv(hidden_channels, hidden_channels,
                       edge_dim=num_edge_features if num_edge_features > 0 else None)
            )
        
        # Link predictor
        self.link_predictor = nn.Sequential(
            nn.Linear(2 * hidden_channels, hidden_channels),
            nn.ReLU(),
            nn.Linear(hidden_channels, 1)
        )
    
    def encode(self, x, edge_index, edge_attr=None):
        """Encode nodes to embeddings."""
        x = self.node_encoder(x)
        
        for conv in self.convs:
            x = F.relu(conv(x, edge_index, edge_attr))
        
        return x
    
    def decode(self, z, edge_index):
        """Decode edge probabilities from node embeddings."""
        # Concatenate source and target node embeddings
        src, dst = edge_index
        edge_embeddings = torch.cat([z[src], z[dst]], dim=-1)
        
        # Predict edge existence probability
        return torch.sigmoid(self.link_predictor(edge_embeddings))
    
    def forward(self, data, negative_samples=True):
        """
        Forward pass with optional negative sampling.
        
        Args:
            data: PyG Data object
            negative_samples: Whether to generate negative samples for training
        """
        x, edge_index = data.x, data.edge_index
        edge_attr = data.edge_attr if hasattr(data, 'edge_attr') else None
        
        # Encode nodes
        z = self.encode(x, edge_index, edge_attr)
        
        # Positive edges
        pos_pred = self.decode(z, edge_index).squeeze()
        
        if negative_samples and self.training:
            # Generate negative edges
            neg_edge_index = negative_sampling(
                edge_index, num_nodes=x.size(0),
                num_neg_samples=edge_index.size(1)
            )
            neg_pred = self.decode(z, neg_edge_index).squeeze()
            
            return pos_pred, neg_pred
        
        return pos_pred
    
    def predict_links(self, data, threshold: float = 0.5):
        """
        Predict all possible links in a graph.
        
        Args:
            data: PyG Data object
            threshold: Probability threshold for edge existence
            
        Returns:
            Predicted edge indices and probabilities
        """
        self.eval()
        with torch.no_grad():
            x, edge_index = data.x, data.edge_index
            edge_attr = data.edge_attr if hasattr(data, 'edge_attr') else None
            
            # Encode nodes
            z = self.encode(x, edge_index, edge_attr)
            
            # Generate all possible edges
            num_nodes = x.size(0)
            all_edges = []
            for i in range(num_nodes):
                for j in range(num_nodes):
                    if i != j:  # No self-loops
                        all_edges.append([i, j])
            
            if not all_edges:
                return torch.tensor([]).reshape(2, 0), torch.tensor([])
            
            all_edges = torch.tensor(all_edges, dtype=torch.long).t().to(x.device)
            
            # Predict
            probs = self.decode(z, all_edges).squeeze()
            
            # Filter by threshold
            mask = probs >= threshold
            predicted_edges = all_edges[:, mask]
            predicted_probs = probs[mask]
            
            return predicted_edges, predicted_probs


class NodeTypePredictor(nn.Module):
    """
    Predicts the type of unit operations (nodes) in a flowsheet.
    
    Given a partial flowsheet structure, predicts what type each unit should be.
    """
    
    def __init__(
        self,
        num_node_features: int,
        num_edge_features: int,
        num_node_types: int,
        hidden_channels: int = 64
    ):
        """
        Initialize Node Type Predictor.
        
        Args:
            num_node_features: Number of input node features
            num_edge_features: Number of edge features
            num_node_types: Number of possible unit types
            hidden_channels: Hidden dimension
        """
        super(NodeTypePredictor, self).__init__()
        
        self.conv1 = GATConv(num_node_features, hidden_channels,
                            edge_dim=num_edge_features if num_edge_features > 0 else None)
        self.conv2 = GATConv(hidden_channels, hidden_channels,
                            edge_dim=num_edge_features if num_edge_features > 0 else None)
        
        self.classifier = nn.Sequential(
            nn.Linear(hidden_channels, hidden_channels // 2),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(hidden_channels // 2, num_node_types)
        )
    
    def forward(self, data):
        """Predict node types."""
        x, edge_index = data.x, data.edge_index
        edge_attr = data.edge_attr if hasattr(data, 'edge_attr') else None
        
        # GNN layers
        x = F.relu(self.conv1(x, edge_index, edge_attr))
        x = F.relu(self.conv2(x, edge_index, edge_attr))
        
        # Classify
        logits = self.classifier(x)
        
        return logits
    
    def predict(self, data):
        """Predict node types with probabilities."""
        self.eval()
        with torch.no_grad():
            logits = self.forward(data)
            probs = F.softmax(logits, dim=-1)
            predicted_types = torch.argmax(probs, dim=-1)
            
            return predicted_types, probs


class FlowsheetGenerator(nn.Module):
    """
    Complete flowsheet generator combining structure and feature prediction.
    
    This is a higher-level model that combines:
    - Number of nodes prediction
    - Link prediction
    - Node type prediction
    - Feature generation
    """
    
    def __init__(
        self,
        num_node_features: int,
        num_edge_features: int,
        num_node_types: int,
        hidden_channels: int = 64,
        latent_dim: int = 32
    ):
        """
        Initialize Flowsheet Generator.
        
        Args:
            num_node_features: Number of node features
            num_edge_features: Number of edge features
            num_node_types: Number of unit operation types
            hidden_channels: Hidden dimension
            latent_dim: Latent space dimension
        """
        super(FlowsheetGenerator, self).__init__()
        
        self.num_node_types = num_node_types
        self.num_node_features = num_node_features
        
        # Condition encoder (for conditional generation)
        self.condition_encoder = nn.Sequential(
            nn.Linear(latent_dim, hidden_channels),
            nn.ReLU(),
            nn.Linear(hidden_channels, hidden_channels)
        )
        
        # Node count predictor
        self.node_count_predictor = nn.Sequential(
            nn.Linear(hidden_channels, hidden_channels // 2),
            nn.ReLU(),
            nn.Linear(hidden_channels // 2, 1)
        )
        
        # Node type generator
        self.node_type_generator = nn.Sequential(
            nn.Linear(hidden_channels, hidden_channels),
            nn.ReLU(),
            nn.Linear(hidden_channels, num_node_types)
        )
        
        # Structure generator (adjacency matrix)
        self.structure_generator = nn.Sequential(
            nn.Linear(hidden_channels + num_node_types, hidden_channels),
            nn.ReLU(),
            nn.Linear(hidden_channels, 1)
        )
        
        # Feature generator
        self.feature_generator = nn.Sequential(
            nn.Linear(hidden_channels + num_node_types, hidden_channels),
            nn.ReLU(),
            nn.Linear(hidden_channels, num_node_features)
        )
    
    def forward(self, condition_vector, num_nodes=None):
        """
        Generate a flowsheet from a condition vector.
        
        Args:
            condition_vector: Latent condition vector [batch_size, latent_dim]
            num_nodes: Optional fixed number of nodes
            
        Returns:
            Generated flowsheet components
        """
        batch_size = condition_vector.size(0)
        
        # Encode condition
        h = self.condition_encoder(condition_vector)
        
        # Predict number of nodes
        if num_nodes is None:
            num_nodes_pred = torch.relu(self.node_count_predictor(h)).squeeze()
            num_nodes = int(num_nodes_pred.mean().item())
        
        # Generate node types
        node_type_logits = self.node_type_generator(h)
        node_type_probs = F.softmax(node_type_logits, dim=-1)
        
        # Expand for each node
        h_expanded = h.unsqueeze(1).repeat(1, num_nodes, 1)
        node_types_expanded = node_type_probs.unsqueeze(1).repeat(1, num_nodes, 1)
        
        # Generate adjacency matrix
        node_pairs = torch.cat([
            h_expanded.unsqueeze(2).repeat(1, 1, num_nodes, 1),
            h_expanded.unsqueeze(1).repeat(1, num_nodes, 1, 1)
        ], dim=-1)
        
        # Simplified adjacency generation
        combined = torch.cat([h_expanded, node_types_expanded], dim=-1)
        adj_logits = self.structure_generator(combined).squeeze(-1)
        
        # Generate node features
        node_features = self.feature_generator(combined)
        
        return {
            'num_nodes': num_nodes,
            'node_type_probs': node_type_probs,
            'adjacency_logits': adj_logits,
            'node_features': node_features
        }
    
    def generate(self, num_graphs: int = 1, device='cpu', **kwargs):
        """
        Generate random flowsheets.
        
        Args:
            num_graphs: Number of flowsheets to generate
            device: Device to generate on
            
        Returns:
            List of generated flowsheet dictionaries
        """
        self.eval()
        with torch.no_grad():
            # Sample random conditions
            conditions = torch.randn(num_graphs, 32).to(device)
            
            # Generate
            outputs = self.forward(conditions, **kwargs)
            
            return outputs

