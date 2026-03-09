"""
Trainer for Graph Generation Models

Training loops and utilities for:
- Graph VAE
- Link prediction
- Node type prediction
- Complete flowsheet generation
"""

import torch
import torch.nn as nn
from torch_geometric.loader import DataLoader
from torch_geometric.utils import to_dense_adj, negative_sampling
from typing import List, Dict, Any, Optional
import numpy as np
from tqdm import tqdm
import logging

from ..evaluation.graph_metrics import (
    link_prediction_metrics,
    node_type_accuracy,
    flowsheet_validity_score,
    batch_evaluate_generated_flowsheets
)

logger = logging.getLogger(__name__)


class GraphVAETrainer:
    """Trainer for Graph Variational Autoencoder"""
    
    def __init__(
        self,
        model: nn.Module,
        train_dataset: List,
        val_dataset: List,
        batch_size: int = 16,
        learning_rate: float = 0.001,
        device: str = 'cpu'
    ):
        """
        Initialize GraphVAE trainer.
        
        Args:
            model: GraphVAE model
            train_dataset: Training dataset
            val_dataset: Validation dataset
            batch_size: Batch size
            learning_rate: Learning rate
            device: Device to train on
        """
        self.model = model.to(device)
        self.device = torch.device(device)
        self.batch_size = batch_size
        
        # Exclude metadata from batching to avoid KeyError
        self.train_loader = DataLoader(
            train_dataset, 
            batch_size=batch_size, 
            shuffle=True,
            exclude_keys=['metadata']
        )
        self.val_loader = DataLoader(
            val_dataset, 
            batch_size=batch_size, 
            shuffle=False,
            exclude_keys=['metadata']
        )
        
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        
        self.history = {
            'train_loss': [],
            'val_loss': [],
            'train_recon_loss': [],
            'val_recon_loss': []
        }
    
    def train_epoch(self) -> Dict[str, float]:
        """Train for one epoch"""
        self.model.train()
        
        total_loss = 0
        total_recon = 0
        num_graphs = 0
        
        for batch in self.train_loader:
            batch = batch.to(self.device)
            
            self.optimizer.zero_grad()
            
            # Forward pass
            adj_logits, node_features, mu, logvar = self.model(batch)
            
            # Get true adjacency and features
            true_adj = to_dense_adj(batch.edge_index, batch=batch.batch)
            true_features = batch.x
            
            # Calculate loss
            loss = self.model.loss_function(
                adj_logits, node_features, mu, logvar,
                true_adj, true_features
            )
            
            # Backward pass
            loss.backward()
            self.optimizer.step()
            
            total_loss += loss.item() * batch.num_graphs
            num_graphs += batch.num_graphs
        
        return {
            'loss': total_loss / num_graphs,
            'recon_loss': total_recon / num_graphs
        }
    
    @torch.no_grad()
    def validate(self) -> Dict[str, float]:
        """Validate model"""
        self.model.eval()
        
        total_loss = 0
        num_graphs = 0
        
        for batch in self.val_loader:
            batch = batch.to(self.device)
            
            # Forward pass
            adj_logits, node_features, mu, logvar = self.model(batch)
            
            # Get true adjacency and features
            true_adj = to_dense_adj(batch.edge_index, batch=batch.batch)
            true_features = batch.x
            
            # Calculate loss
            loss = self.model.loss_function(
                adj_logits, node_features, mu, logvar,
                true_adj, true_features
            )
            
            total_loss += loss.item() * batch.num_graphs
            num_graphs += batch.num_graphs
        
        return {'loss': total_loss / num_graphs}
    
    def train(self, num_epochs: int = 100, verbose: bool = True) -> Dict[str, List[float]]:
        """
        Train the model.
        
        Args:
            num_epochs: Number of epochs
            verbose: Whether to show progress
            
        Returns:
            Training history
        """
        pbar = tqdm(range(num_epochs), desc="Training GraphVAE") if verbose else range(num_epochs)
        
        for epoch in pbar:
            # Train
            train_metrics = self.train_epoch()
            
            # Validate
            val_metrics = self.validate()
            
            # Update history
            self.history['train_loss'].append(train_metrics['loss'])
            self.history['val_loss'].append(val_metrics['loss'])
            
            if verbose:
                pbar.set_postfix({
                    'train_loss': f"{train_metrics['loss']:.4f}",
                    'val_loss': f"{val_metrics['loss']:.4f}"
                })
        
        return self.history


class LinkPredictionTrainer:
    """Trainer for Link Prediction models"""
    
    def __init__(
        self,
        model: nn.Module,
        train_dataset: List,
        val_dataset: List,
        batch_size: int = 16,
        learning_rate: float = 0.001,
        device: str = 'cpu'
    ):
        """
        Initialize Link Prediction trainer.
        
        Args:
            model: LinkPredictionGNN model
            train_dataset: Training dataset
            val_dataset: Validation dataset
            batch_size: Batch size
            learning_rate: Learning rate
            device: Device to train on
        """
        self.model = model.to(device)
        self.device = torch.device(device)
        
        # Exclude metadata from batching to avoid KeyError
        self.train_loader = DataLoader(
            train_dataset, 
            batch_size=batch_size, 
            shuffle=True,
            exclude_keys=['metadata']
        )
        self.val_loader = DataLoader(
            val_dataset, 
            batch_size=batch_size, 
            shuffle=False,
            exclude_keys=['metadata']
        )
        
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        self.criterion = nn.BCELoss()
        
        self.history = {
            'train_loss': [],
            'val_loss': [],
            'train_precision': [],
            'val_precision': [],
            'train_recall': [],
            'val_recall': []
        }
    
    def train_epoch(self) -> Dict[str, float]:
        """Train for one epoch"""
        self.model.train()
        
        total_loss = 0
        all_metrics = []
        
        for batch in self.train_loader:
            batch = batch.to(self.device)
            
            self.optimizer.zero_grad()
            
            # Forward pass with negative sampling
            pos_pred, neg_pred = self.model(batch, negative_samples=True)
            
            # Create labels
            pos_labels = torch.ones_like(pos_pred)
            neg_labels = torch.zeros_like(neg_pred)
            
            # Combine predictions and labels
            predictions = torch.cat([pos_pred, neg_pred])
            labels = torch.cat([pos_labels, neg_labels])
            
            # Calculate loss
            loss = self.criterion(predictions, labels)
            
            # Backward pass
            loss.backward()
            self.optimizer.step()
            
            total_loss += loss.item()
        
        return {'loss': total_loss / len(self.train_loader)}
    
    @torch.no_grad()
    def validate(self) -> Dict[str, float]:
        """Validate model"""
        self.model.eval()
        
        total_loss = 0
        all_precisions = []
        all_recalls = []
        
        for batch in self.val_loader:
            batch = batch.to(self.device)
            
            # Predict links
            pred_edges, pred_probs = self.model.predict_links(batch, threshold=0.5)
            
            # Calculate metrics
            metrics = link_prediction_metrics(
                pred_edges, batch.edge_index,
                pred_probs, batch.num_nodes
            )
            
            all_precisions.append(metrics['precision'])
            all_recalls.append(metrics['recall'])
        
        return {
            'loss': total_loss / len(self.val_loader) if len(self.val_loader) > 0 else 0,
            'precision': np.mean(all_precisions) if all_precisions else 0,
            'recall': np.mean(all_recalls) if all_recalls else 0
        }
    
    def train(self, num_epochs: int = 100, verbose: bool = True) -> Dict[str, List[float]]:
        """Train the model"""
        pbar = tqdm(range(num_epochs), desc="Training Link Predictor") if verbose else range(num_epochs)
        
        for epoch in pbar:
            train_metrics = self.train_epoch()
            val_metrics = self.validate()
            
            self.history['train_loss'].append(train_metrics['loss'])
            self.history['val_loss'].append(val_metrics['loss'])
            self.history['val_precision'].append(val_metrics['precision'])
            self.history['val_recall'].append(val_metrics['recall'])
            
            if verbose:
                pbar.set_postfix({
                    'train_loss': f"{train_metrics['loss']:.4f}",
                    'val_prec': f"{val_metrics['precision']:.3f}",
                    'val_rec': f"{val_metrics['recall']:.3f}"
                })
        
        return self.history


class NodeTypePredictionTrainer:
    """Trainer for Node Type Prediction"""
    
    def __init__(
        self,
        model: nn.Module,
        train_dataset: List,
        val_dataset: List,
        batch_size: int = 16,
        learning_rate: float = 0.001,
        device: str = 'cpu'
    ):
        """Initialize Node Type Prediction trainer"""
        self.model = model.to(device)
        self.device = torch.device(device)
        
        # Exclude metadata from batching to avoid KeyError
        self.train_loader = DataLoader(
            train_dataset, 
            batch_size=batch_size, 
            shuffle=True,
            exclude_keys=['metadata']
        )
        self.val_loader = DataLoader(
            val_dataset, 
            batch_size=batch_size, 
            shuffle=False,
            exclude_keys=['metadata']
        )
        
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        self.criterion = nn.CrossEntropyLoss()
        
        self.history = {
            'train_loss': [],
            'val_loss': [],
            'train_accuracy': [],
            'val_accuracy': []
        }
    
    def train_epoch(self) -> Dict[str, float]:
        """Train for one epoch"""
        self.model.train()
        
        total_loss = 0
        correct = 0
        total = 0
        
        for batch in self.train_loader:
            batch = batch.to(self.device)
            
            # Get node type labels (assuming they're the first feature)
            node_types = batch.x[:, 0].long()
            
            self.optimizer.zero_grad()
            
            # Forward pass
            logits = self.model(batch)
            
            # Calculate loss
            loss = self.criterion(logits, node_types)
            
            # Backward pass
            loss.backward()
            self.optimizer.step()
            
            # Calculate accuracy
            pred = logits.argmax(dim=-1)
            correct += (pred == node_types).sum().item()
            total += node_types.size(0)
            
            total_loss += loss.item()
        
        return {
            'loss': total_loss / len(self.train_loader),
            'accuracy': correct / total if total > 0 else 0
        }
    
    @torch.no_grad()
    def validate(self) -> Dict[str, float]:
        """Validate model"""
        self.model.eval()
        
        total_loss = 0
        correct = 0
        total = 0
        
        for batch in self.val_loader:
            batch = batch.to(self.device)
            
            node_types = batch.x[:, 0].long()
            
            logits = self.model(batch)
            loss = self.criterion(logits, node_types)
            
            pred = logits.argmax(dim=-1)
            correct += (pred == node_types).sum().item()
            total += node_types.size(0)
            
            total_loss += loss.item()
        
        return {
            'loss': total_loss / len(self.val_loader),
            'accuracy': correct / total if total > 0 else 0
        }
    
    def train(self, num_epochs: int = 100, verbose: bool = True) -> Dict[str, List[float]]:
        """Train the model"""
        pbar = tqdm(range(num_epochs), desc="Training Node Type Predictor") if verbose else range(num_epochs)
        
        for epoch in pbar:
            train_metrics = self.train_epoch()
            val_metrics = self.validate()
            
            self.history['train_loss'].append(train_metrics['loss'])
            self.history['val_loss'].append(val_metrics['loss'])
            self.history['train_accuracy'].append(train_metrics['accuracy'])
            self.history['val_accuracy'].append(val_metrics['accuracy'])
            
            if verbose:
                pbar.set_postfix({
                    'train_loss': f"{train_metrics['loss']:.4f}",
                    'train_acc': f"{train_metrics['accuracy']:.3f}",
                    'val_acc': f"{val_metrics['accuracy']:.3f}"
                })
        
        return self.history

