"""
Utility functions for training
"""

import random
from typing import List, Tuple
from torch_geometric.data import Data
import torch
import logging

logger = logging.getLogger(__name__)


def train_test_split_graphs(
    dataset: List[Data],
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42
) -> Tuple[List[Data], List[Data], List[Data]]:
    """
    Split dataset into train, validation, and test sets.
    
    Args:
        dataset: List of Data objects
        train_ratio: Ratio of training data
        val_ratio: Ratio of validation data
        test_ratio: Ratio of test data
        seed: Random seed
        
    Returns:
        Tuple of (train_dataset, val_dataset, test_dataset)
    """
    assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, "Ratios must sum to 1"
    
    random.seed(seed)
    indices = list(range(len(dataset)))
    random.shuffle(indices)
    
    n = len(dataset)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    
    train_indices = indices[:n_train]
    val_indices = indices[n_train:n_train + n_val]
    test_indices = indices[n_train + n_val:]
    
    train_dataset = [dataset[i] for i in train_indices]
    val_dataset = [dataset[i] for i in val_indices]
    test_dataset = [dataset[i] for i in test_indices]
    
    logger.info(f"Split dataset: {len(train_dataset)} train, {len(val_dataset)} val, {len(test_dataset)} test")
    
    return train_dataset, val_dataset, test_dataset


class EarlyStopping:
    """Early stopping to prevent overfitting"""
    
    def __init__(self, patience: int = 10, min_delta: float = 0.0, mode: str = 'min'):
        """
        Initialize early stopping.
        
        Args:
            patience: Number of epochs to wait before stopping
            min_delta: Minimum change to qualify as improvement
            mode: 'min' or 'max' - whether to minimize or maximize metric
        """
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.best_model_state = None
        
    def __call__(self, score: float, model: torch.nn.Module) -> bool:
        """
        Check if training should stop.
        
        Args:
            score: Current validation score
            model: Model to save state
            
        Returns:
            True if training should stop, False otherwise
        """
        if self.best_score is None:
            self.best_score = score
            self.best_model_state = model.state_dict()
            return False
        
        if self.mode == 'min':
            improved = score < (self.best_score - self.min_delta)
        else:
            improved = score > (self.best_score + self.min_delta)
        
        if improved:
            self.best_score = score
            self.best_model_state = model.state_dict()
            self.counter = 0
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True
                logger.info(f"Early stopping triggered after {self.counter} epochs without improvement")
                return True
        
        return False
    
    def load_best_model(self, model: torch.nn.Module):
        """Load the best model state"""
        if self.best_model_state is not None:
            model.load_state_dict(self.best_model_state)

