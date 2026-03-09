"""
Production-ready flowsheet generation with optimized threshold.

This module implements the immediate deployment strategy for graph generation,
using the baseline model with an optimized threshold for improved accuracy.
"""

import torch
import numpy as np
import networkx as nx
from typing import Tuple, Dict, Optional, List
from torch_geometric.data import Data


class ProductionFlowsheetGenerator:
    """
    Production-ready flowsheet generator with optimal threshold.
    
    This class wraps a trained GraphVAE model and applies post-processing
    optimizations for improved generation quality.
    """
    
    def __init__(
        self,
        model,
        optimal_threshold: float = 0.75,
        device: str = 'cpu',
        apply_constraints: bool = False,
        max_in_degree: Optional[int] = None,
        max_out_degree: Optional[int] = None
    ):
        """
        Initialize the production generator.
        
        Args:
            model: Trained GraphVAE model
            optimal_threshold: Optimized threshold for edge prediction
            device: Device to run generation on ('cpu' or 'cuda')
            apply_constraints: Whether to apply domain constraints
            max_in_degree: Maximum in-degree for constraint validation
            max_out_degree: Maximum out-degree for constraint validation
        """
        self.model = model
        self.optimal_threshold = optimal_threshold
        self.device = device
        self.apply_constraints = apply_constraints
        self.max_in_degree = max_in_degree
        self.max_out_degree = max_out_degree
        
        # Move model to device and set to eval mode
        self.model.to(device)
        self.model.eval()
    
    def generate_flowsheet(
        self,
        num_nodes: int = 88,
        return_metrics: bool = True
    ) -> Tuple[np.ndarray, np.ndarray, Optional[Dict]]:
        """
        Generate a single flowsheet with optimized threshold.
        
        Args:
            num_nodes: Number of nodes in the flowsheet
            return_metrics: Whether to compute quality metrics
            
        Returns:
            adj_matrix: Binary adjacency matrix
            node_features: Node feature matrix
            metrics: Quality metrics (if return_metrics=True)
        """
        # Step 1: Generate with trained model
        with torch.no_grad():
            adj_matrices, node_features = self.model.generate(
                num_graphs=1,
                num_nodes=num_nodes,
                device=self.device
            )
        
        # Step 2: Apply optimal threshold
        adj_np = adj_matrices[0].cpu().numpy()
        node_feat_np = node_features[0].cpu().numpy()
        
        # Step 3: Apply constraints (if enabled)
        if self.apply_constraints:
            adj_binary, violations = self._validate_and_correct(
                adj_np,
                threshold=self.optimal_threshold
            )
        else:
            adj_binary = (adj_np > self.optimal_threshold).astype(int)
        
        # Step 4: Compute quality metrics
        metrics = None
        if return_metrics:
            metrics = self._compute_quality_metrics(adj_binary)
        
        return adj_binary, node_feat_np, metrics
    
    def generate_batch(
        self,
        num_flowsheets: int = 10,
        num_nodes: int = 88
    ) -> List[Tuple[np.ndarray, np.ndarray, Dict]]:
        """
        Generate multiple flowsheets.
        
        Args:
            num_flowsheets: Number of flowsheets to generate
            num_nodes: Number of nodes per flowsheet
            
        Returns:
            List of (adj_matrix, node_features, metrics) tuples
        """
        results = []
        for i in range(num_flowsheets):
            adj, features, metrics = self.generate_flowsheet(
                num_nodes=num_nodes,
                return_metrics=True
            )
            results.append((adj, features, metrics))
        
        return results
    
    def _validate_and_correct(
        self,
        adj_matrix: np.ndarray,
        threshold: float
    ) -> Tuple[np.ndarray, Dict]:
        """
        Apply chemical engineering constraints to generated flowsheet.
        
        Args:
            adj_matrix: Adjacency matrix (continuous probabilities)
            threshold: Threshold for edge existence
            
        Returns:
            corrected_adj: Corrected binary adjacency matrix
            violations: Dict of constraint violations
        """
        adj_binary = (adj_matrix > threshold).astype(int)
        corrected_adj = adj_binary.copy()
        violations = {'degree': 0, 'isolated': 0}
        
        num_nodes = adj_binary.shape[0]
        
        # Calculate degrees
        in_degrees = np.sum(adj_binary, axis=0)
        out_degrees = np.sum(adj_binary, axis=1)
        
        # Constraint 1: Enforce maximum in-degree
        if self.max_in_degree:
            for node in range(num_nodes):
                if in_degrees[node] > self.max_in_degree:
                    incoming_edges = adj_matrix[:, node]
                    if np.sum(incoming_edges > 0) > self.max_in_degree:
                        threshold_val = np.sort(incoming_edges)[-self.max_in_degree]
                        corrected_adj[:, node] = (incoming_edges >= threshold_val).astype(int)
                        violations['degree'] += 1
        
        # Constraint 2: Enforce maximum out-degree
        if self.max_out_degree:
            for node in range(num_nodes):
                if out_degrees[node] > self.max_out_degree:
                    outgoing_edges = adj_matrix[node, :]
                    if np.sum(outgoing_edges > 0) > self.max_out_degree:
                        threshold_val = np.sort(outgoing_edges)[-self.max_out_degree]
                        corrected_adj[node, :] = (outgoing_edges >= threshold_val).astype(int)
                        violations['degree'] += 1
        
        # Count isolated nodes
        total_degrees = np.sum(corrected_adj, axis=0) + np.sum(corrected_adj, axis=1)
        violations['isolated'] = len(np.where(total_degrees == 0)[0])
        
        return corrected_adj, violations
    
    def _compute_quality_metrics(self, adj_matrix: np.ndarray) -> Dict:
        """
        Compute quality metrics for a generated flowsheet.
        
        Args:
            adj_matrix: Binary adjacency matrix
            
        Returns:
            Dictionary of quality metrics
        """
        metrics = {}
        
        # Create NetworkX graph
        G = nx.from_numpy_array(adj_matrix, create_using=nx.DiGraph)
        
        # Basic stats
        metrics['num_nodes'] = len(G.nodes())
        metrics['num_edges'] = len(G.edges())
        metrics['density'] = nx.density(G) if len(G.nodes()) > 0 else 0
        
        # Connectivity
        metrics['is_weakly_connected'] = nx.is_weakly_connected(G) if len(G.nodes()) > 0 else False
        metrics['num_components'] = nx.number_weakly_connected_components(G) if len(G.nodes()) > 0 else 0
        
        # Degree distribution
        if len(G.nodes()) > 0:
            in_degrees = [G.in_degree(n) for n in G.nodes()]
            out_degrees = [G.out_degree(n) for n in G.nodes()]
            metrics['avg_in_degree'] = np.mean(in_degrees)
            metrics['avg_out_degree'] = np.mean(out_degrees)
            metrics['max_in_degree'] = np.max(in_degrees)
            metrics['max_out_degree'] = np.max(out_degrees)
            
            # Sources and sinks
            metrics['num_sources'] = sum(1 for n in G.nodes() if G.in_degree(n) == 0)
            metrics['num_sinks'] = sum(1 for n in G.nodes() if G.out_degree(n) == 0)
        else:
            metrics['avg_in_degree'] = 0
            metrics['avg_out_degree'] = 0
            metrics['max_in_degree'] = 0
            metrics['max_out_degree'] = 0
            metrics['num_sources'] = 0
            metrics['num_sinks'] = 0
        
        # Cycles
        try:
            metrics['has_cycles'] = not nx.is_directed_acyclic_graph(G)
            if metrics['has_cycles'] and len(G.nodes()) > 0:
                cycles = list(nx.simple_cycles(G))
                metrics['num_cycles'] = len(cycles)
            else:
                metrics['num_cycles'] = 0
        except:
            metrics['has_cycles'] = False
            metrics['num_cycles'] = 0
        
        return metrics
    
    def set_optimal_threshold(self, threshold: float):
        """Update the optimal threshold."""
        self.optimal_threshold = threshold
    
    def enable_constraints(self, max_in: int, max_out: int):
        """Enable constraint-based post-processing."""
        self.apply_constraints = True
        self.max_in_degree = max_in
        self.max_out_degree = max_out
    
    def disable_constraints(self):
        """Disable constraint-based post-processing."""
        self.apply_constraints = False


def quick_generate(
    model,
    num_flowsheets: int = 1,
    num_nodes: int = 88,
    optimal_threshold: float = 0.75,
    device: str = 'cpu'
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """
    Quick generation function for immediate use.
    
    Args:
        model: Trained GraphVAE model
        num_flowsheets: Number of flowsheets to generate
        num_nodes: Number of nodes per flowsheet
        optimal_threshold: Optimized threshold
        device: Device to use
        
    Returns:
        List of (adj_matrix, node_features) tuples
    """
    generator = ProductionFlowsheetGenerator(
        model=model,
        optimal_threshold=optimal_threshold,
        device=device,
        apply_constraints=False
    )
    
    results = []
    for _ in range(num_flowsheets):
        adj, features, _ = generator.generate_flowsheet(
            num_nodes=num_nodes,
            return_metrics=False
        )
        results.append((adj, features))
    
    return results

