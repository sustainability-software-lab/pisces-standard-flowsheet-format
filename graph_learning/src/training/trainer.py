"""
Trainer: Training loop and validation for GNN models
"""

import torch
import torch.nn as nn
from torch_geometric.loader import DataLoader
from typing import List, Dict, Any, Optional
import numpy as np
from tqdm import tqdm
import logging
from pathlib import Path

from .utils import EarlyStopping

logger = logging.getLogger(__name__)


class Trainer:
    """Trainer for ProcessGNN models"""
    
    def __init__(
        self,
        model: nn.Module,
        train_dataset: List,
        val_dataset: List,
        batch_size: int = 32,
        learning_rate: float = 0.001,
        device: str = 'cpu',
        checkpoint_dir: Optional[str] = None
    ):
        """
        Initialize trainer.
        
        Args:
            model: ProcessGNN model
            train_dataset: Training dataset
            val_dataset: Validation dataset
            batch_size: Batch size
            learning_rate: Learning rate
            device: Device to train on ('cpu' or 'cuda')
            checkpoint_dir: Directory to save checkpoints
        """
        self.model = model
        self.train_dataset = train_dataset
        self.val_dataset = val_dataset
        self.batch_size = batch_size
        self.device = torch.device(device)
        
        # Move model to device
        self.model.to(self.device)
        
        # Create data loaders (exclude metadata to avoid batching issues)
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
        
        # Optimizer and loss
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        self.criterion = nn.MSELoss()
        
        # Checkpoint directory
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        if self.checkpoint_dir:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        # Training history
        self.history = {
            'train_loss': [],
            'val_loss': [],
            'train_mae': [],
            'val_mae': [],
            'train_r2': [],
            'val_r2': []
        }
    
    def train_epoch(self) -> Dict[str, float]:
        """Train for one epoch"""
        self.model.train()
        
        total_loss = 0
        all_preds = []
        all_targets = []
        
        for batch in self.train_loader:
            batch = batch.to(self.device)
            
            # Forward pass
            self.optimizer.zero_grad()
            out = self.model(batch)
            
            # Compute loss
            loss = self.criterion(out, batch.y.view(-1, 1))
            
            # Backward pass
            loss.backward()
            self.optimizer.step()
            
            total_loss += loss.item() * batch.num_graphs
            all_preds.extend(out.detach().cpu().numpy())
            all_targets.extend(batch.y.cpu().numpy())
        
        # Calculate metrics
        avg_loss = total_loss / len(self.train_dataset)
        mae = np.mean(np.abs(np.array(all_preds) - np.array(all_targets)))
        r2 = self._calculate_r2(np.array(all_targets), np.array(all_preds))
        
        return {
            'loss': avg_loss,
            'mae': mae,
            'r2': r2
        }
    
    @torch.no_grad()
    def validate(self) -> Dict[str, float]:
        """Validate model"""
        self.model.eval()
        
        total_loss = 0
        all_preds = []
        all_targets = []
        
        for batch in self.val_loader:
            batch = batch.to(self.device)
            
            # Forward pass
            out = self.model(batch)
            
            # Compute loss
            loss = self.criterion(out, batch.y.view(-1, 1))
            
            total_loss += loss.item() * batch.num_graphs
            all_preds.extend(out.cpu().numpy())
            all_targets.extend(batch.y.cpu().numpy())
        
        # Calculate metrics
        avg_loss = total_loss / len(self.val_dataset)
        mae = np.mean(np.abs(np.array(all_preds) - np.array(all_targets)))
        r2 = self._calculate_r2(np.array(all_targets), np.array(all_preds))
        
        return {
            'loss': avg_loss,
            'mae': mae,
            'r2': r2
        }
    
    def train(
        self,
        num_epochs: int = 100,
        early_stopping_patience: int = 20,
        verbose: bool = True
    ) -> Dict[str, List[float]]:
        """
        Train the model.
        
        Args:
            num_epochs: Number of epochs to train
            early_stopping_patience: Patience for early stopping
            verbose: Whether to print progress
            
        Returns:
            Training history
        """
        early_stopping = EarlyStopping(patience=early_stopping_patience, mode='min')
        
        pbar = tqdm(range(num_epochs), desc="Training") if verbose else range(num_epochs)
        
        for epoch in pbar:
            # Train
            train_metrics = self.train_epoch()
            
            # Validate
            val_metrics = self.validate()
            
            # Update history
            self.history['train_loss'].append(train_metrics['loss'])
            self.history['val_loss'].append(val_metrics['loss'])
            self.history['train_mae'].append(train_metrics['mae'])
            self.history['val_mae'].append(val_metrics['mae'])
            self.history['train_r2'].append(train_metrics['r2'])
            self.history['val_r2'].append(val_metrics['r2'])
            
            # Update progress bar
            if verbose:
                pbar.set_postfix({
                    'train_loss': f"{train_metrics['loss']:.4f}",
                    'val_loss': f"{val_metrics['loss']:.4f}",
                    'val_mae': f"{val_metrics['mae']:.2f}",
                    'val_r2': f"{val_metrics['r2']:.4f}"
                })
            
            # Early stopping
            if early_stopping(val_metrics['loss'], self.model):
                logger.info(f"Early stopping at epoch {epoch + 1}")
                break
            
            # Save checkpoint every 10 epochs
            if self.checkpoint_dir and (epoch + 1) % 10 == 0:
                self.save_checkpoint(epoch + 1)
        
        # Load best model
        early_stopping.load_best_model(self.model)
        
        # Save final model
        if self.checkpoint_dir:
            self.save_checkpoint('final')
        
        return self.history
    
    def save_checkpoint(self, epoch: int):
        """Save model checkpoint"""
        if self.checkpoint_dir is None:
            return
        
        checkpoint_path = self.checkpoint_dir / f'checkpoint_epoch_{epoch}.pt'
        torch.save({
            'epoch': epoch,
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'history': self.history,
        }, checkpoint_path)
        
        logger.info(f"Saved checkpoint to {checkpoint_path}")
    
    def load_checkpoint(self, checkpoint_path: str):
        """Load model checkpoint"""
        checkpoint = torch.load(checkpoint_path, map_location=self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        self.history = checkpoint.get('history', self.history)
        
        logger.info(f"Loaded checkpoint from {checkpoint_path}")
    
    @staticmethod
    def _calculate_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """Calculate R² score"""
        ss_res = np.sum((y_true - y_pred) ** 2)
        ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
        return r2
    
    @torch.no_grad()
    def predict(self, dataset: List) -> np.ndarray:
        """
        Make predictions on a dataset.
        
        Args:
            dataset: List of Data objects
            
        Returns:
            Array of predictions
        """
        self.model.eval()
        
        loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=False, exclude_keys=['metadata'])
        predictions = []
        
        for batch in loader:
            batch = batch.to(self.device)
            out = self.model(batch)
            predictions.extend(out.cpu().numpy())
        
        return np.array(predictions)

