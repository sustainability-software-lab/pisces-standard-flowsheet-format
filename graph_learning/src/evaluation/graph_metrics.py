"""
Graph Similarity Metrics for Flowsheet Structure Evaluation

Metrics for comparing generated flowsheets with ground truth or reference structures.
"""

import torch
import numpy as np
from torch_geometric.data import Data
from torch_geometric.utils import to_dense_adj, to_networkx
import networkx as nx
from typing import List, Dict, Tuple
from scipy.optimize import linear_sum_assignment


def graph_edit_distance(data1: Data, data2: Data, normalize: bool = True) -> float:
    """
    Calculate Graph Edit Distance (GED) between two flowsheets.
    
    GED counts the minimum number of edit operations (add/remove nodes/edges)
    needed to transform one graph into another.
    
    Args:
        data1: First graph
        data2: Second graph
        normalize: Whether to normalize by sum of node counts
        
    Returns:
        Graph edit distance
    """
    # Convert to NetworkX
    G1 = to_networkx(data1, to_undirected=False)
    G2 = to_networkx(data2, to_undirected=False)
    
    # Calculate GED (this can be expensive for large graphs)
    try:
        # Use approximation for speed
        ged = nx.graph_edit_distance(
            G1, G2,
            node_match=None,  # Don't match node attributes for now
            edge_match=None,
            timeout=10
        )
        
        if normalize:
            total_nodes = len(G1.nodes()) + len(G2.nodes())
            if total_nodes > 0:
                ged = ged / total_nodes
        
        return ged
    except:
        # Fallback: use simpler metrics
        return abs(len(G1.nodes()) - len(G2.nodes())) / max(len(G1.nodes()), len(G2.nodes()), 1)


def adjacency_matrix_similarity(adj1: torch.Tensor, adj2: torch.Tensor) -> Dict[str, float]:
    """
    Calculate similarity between two adjacency matrices.
    
    Args:
        adj1: First adjacency matrix [N, N]
        adj2: Second adjacency matrix [M, M]
        
    Returns:
        Dictionary of similarity metrics
    """
    # Ensure same size (pad if needed)
    max_size = max(adj1.size(0), adj2.size(0))
    
    if adj1.size(0) < max_size:
        padding = max_size - adj1.size(0)
        adj1 = torch.nn.functional.pad(adj1, (0, padding, 0, padding))
    
    if adj2.size(0) < max_size:
        padding = max_size - adj2.size(0)
        adj2 = torch.nn.functional.pad(adj2, (0, padding, 0, padding))
    
    # Flatten
    adj1_flat = adj1.flatten()
    adj2_flat = adj2.flatten()
    
    # Calculate metrics
    # 1. Hamming similarity (for binary adjacency)
    adj1_bin = (adj1_flat > 0.5).float()
    adj2_bin = (adj2_flat > 0.5).float()
    hamming = (adj1_bin == adj2_bin).float().mean().item()
    
    # 2. Cosine similarity
    cosine = torch.nn.functional.cosine_similarity(
        adj1_flat.unsqueeze(0), 
        adj2_flat.unsqueeze(0)
    ).item()
    
    # 3. MSE
    mse = torch.nn.functional.mse_loss(adj1_flat, adj2_flat).item()
    
    return {
        'hamming_similarity': hamming,
        'cosine_similarity': cosine,
        'mse': mse
    }


def node_type_accuracy(pred_types: torch.Tensor, true_types: torch.Tensor) -> Dict[str, float]:
    """
    Calculate accuracy of node type predictions.
    
    Args:
        pred_types: Predicted node types [N]
        true_types: True node types [N]
        
    Returns:
        Dictionary with accuracy metrics
    """
    correct = (pred_types == true_types).sum().item()
    total = len(true_types)
    
    accuracy = correct / total if total > 0 else 0.0
    
    # Per-class accuracy
    unique_types = torch.unique(true_types)
    per_class_acc = {}
    
    for node_type in unique_types:
        mask = (true_types == node_type)
        if mask.sum() > 0:
            class_correct = ((pred_types == true_types) & mask).sum().item()
            class_total = mask.sum().item()
            per_class_acc[f'type_{node_type.item()}'] = class_correct / class_total
    
    return {
        'overall_accuracy': accuracy,
        **per_class_acc
    }


def structural_metrics(data: Data) -> Dict[str, float]:
    """
    Calculate structural properties of a flowsheet graph.
    
    Args:
        data: PyG Data object
        
    Returns:
        Dictionary of structural metrics
    """
    G = to_networkx(data, to_undirected=False)
    
    metrics = {
        'num_nodes': G.number_of_nodes(),
        'num_edges': G.number_of_edges(),
        'density': nx.density(G),
        'avg_degree': sum(dict(G.degree()).values()) / G.number_of_nodes() if G.number_of_nodes() > 0 else 0,
    }
    
    # Check if strongly connected
    if nx.is_strongly_connected(G):
        metrics['is_strongly_connected'] = 1.0
        metrics['diameter'] = nx.diameter(G)
    else:
        metrics['is_strongly_connected'] = 0.0
        metrics['num_strongly_connected_components'] = nx.number_strongly_connected_components(G)
    
    # Check if DAG (Directed Acyclic Graph - important for process flowsheets!)
    metrics['is_dag'] = 1.0 if nx.is_directed_acyclic_graph(G) else 0.0
    
    return metrics


def compare_structural_distributions(
    generated_graphs: List[Data],
    reference_graphs: List[Data]
) -> Dict[str, float]:
    """
    Compare distributions of structural properties between generated and reference graphs.
    
    Args:
        generated_graphs: List of generated graphs
        reference_graphs: List of reference graphs
        
    Returns:
        Dictionary of comparison metrics
    """
    # Collect structural metrics for both sets
    gen_metrics = [structural_metrics(g) for g in generated_graphs]
    ref_metrics = [structural_metrics(g) for g in reference_graphs]
    
    results = {}
    
    # Compare distributions of key metrics
    for key in ['num_nodes', 'num_edges', 'density', 'avg_degree']:
        gen_values = [m[key] for m in gen_metrics]
        ref_values = [m[key] for m in ref_metrics]
        
        # Mean absolute difference
        gen_mean = np.mean(gen_values)
        ref_mean = np.mean(ref_values)
        results[f'{key}_mean_diff'] = abs(gen_mean - ref_mean)
        
        # Standard deviation difference
        gen_std = np.std(gen_values)
        ref_std = np.std(ref_values)
        results[f'{key}_std_diff'] = abs(gen_std - ref_std)
    
    # Percentage of valid flowsheets (DAGs)
    gen_dag_ratio = np.mean([m['is_dag'] for m in gen_metrics])
    ref_dag_ratio = np.mean([m['is_dag'] for m in ref_metrics])
    results['dag_ratio_diff'] = abs(gen_dag_ratio - ref_dag_ratio)
    
    return results


def link_prediction_metrics(
    pred_edges: torch.Tensor,
    true_edges: torch.Tensor,
    pred_probs: torch.Tensor = None,
    num_nodes: int = None
) -> Dict[str, float]:
    """
    Calculate metrics for link prediction task.
    
    Args:
        pred_edges: Predicted edges [2, E_pred]
        true_edges: True edges [2, E_true]
        pred_probs: Edge probabilities (optional)
        num_nodes: Total number of nodes
        
    Returns:
        Dictionary of metrics
    """
    # Convert to sets for comparison
    pred_set = set(map(tuple, pred_edges.t().tolist()))
    true_set = set(map(tuple, true_edges.t().tolist()))
    
    # True positives, false positives, false negatives
    tp = len(pred_set & true_set)
    fp = len(pred_set - true_set)
    fn = len(true_set - pred_set)
    
    # Metrics
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    metrics = {
        'precision': precision,
        'recall': recall,
        'f1_score': f1,
        'num_predicted_edges': len(pred_set),
        'num_true_edges': len(true_set),
        'num_correct_edges': tp
    }
    
    # If probabilities provided, calculate AUC-like metrics
    if pred_probs is not None and num_nodes is not None:
        # Calculate edge existence scores for all possible edges
        all_possible_edges = []
        edge_labels = []
        
        for i in range(num_nodes):
            for j in range(num_nodes):
                if i != j:
                    all_possible_edges.append([i, j])
                    edge_labels.append(1 if (i, j) in true_set else 0)
        
        if len(edge_labels) > 0:
            # Match predictions to all possible edges
            pred_scores = torch.zeros(len(all_possible_edges))
            for idx, edge in enumerate(all_possible_edges):
                edge_tuple = tuple(edge)
                if edge_tuple in pred_set:
                    # Find this edge in pred_edges
                    matches = (pred_edges[0] == edge[0]) & (pred_edges[1] == edge[1])
                    if matches.any():
                        pred_idx = matches.nonzero()[0].item()
                        pred_scores[idx] = pred_probs[pred_idx]
            
            # Simple threshold-based accuracy
            metrics['edge_detection_accuracy'] = (
                (pred_scores > 0.5).long() == torch.tensor(edge_labels)
            ).float().mean().item()
    
    return metrics


def flowsheet_validity_score(data: Data, rules: Dict = None) -> Dict[str, float]:
    """
    Check if a generated flowsheet satisfies chemical engineering constraints.
    
    Args:
        data: Generated flowsheet
        rules: Dictionary of validation rules
        
    Returns:
        Dictionary of validity scores
    """
    G = to_networkx(data, to_undirected=False)
    
    scores = {}
    
    # Rule 1: Must be a DAG (no cycles - materials can't flow backwards in time)
    scores['is_dag'] = 1.0 if nx.is_directed_acyclic_graph(G) else 0.0
    
    # Rule 2: Must have at least one source node (feed stream)
    in_degrees = dict(G.in_degree())
    num_sources = sum(1 for deg in in_degrees.values() if deg == 0)
    scores['has_source'] = 1.0 if num_sources > 0 else 0.0
    
    # Rule 3: Must have at least one sink node (product stream)
    out_degrees = dict(G.out_degree())
    num_sinks = sum(1 for deg in out_degrees.values() if deg == 0)
    scores['has_sink'] = 1.0 if num_sinks > 0 else 0.0
    
    # Rule 4: No isolated nodes
    isolated = list(nx.isolates(G))
    scores['no_isolated_nodes'] = 1.0 if len(isolated) == 0 else 0.0
    
    # Rule 5: Reasonable size (not too large or small)
    num_nodes = G.number_of_nodes()
    scores['reasonable_size'] = 1.0 if 5 <= num_nodes <= 200 else 0.0
    
    # Rule 6: Reasonable connectivity (avg degree between 1 and 5)
    avg_degree = sum(dict(G.degree()).values()) / num_nodes if num_nodes > 0 else 0
    scores['reasonable_connectivity'] = 1.0 if 1.0 <= avg_degree <= 5.0 else 0.0
    
    # Overall validity score (average of all rules)
    scores['overall_validity'] = np.mean(list(scores.values()))
    
    return scores


def batch_evaluate_generated_flowsheets(
    generated_graphs: List[Data],
    reference_graphs: List[Data] = None
) -> Dict[str, any]:
    """
    Comprehensive evaluation of generated flowsheets.
    
    Args:
        generated_graphs: List of generated flowsheets
        reference_graphs: Optional list of reference flowsheets for comparison
        
    Returns:
        Dictionary with all evaluation metrics
    """
    results = {
        'num_generated': len(generated_graphs),
        'structural_properties': {},
        'validity_scores': {},
    }
    
    # Structural properties
    gen_struct = [structural_metrics(g) for g in generated_graphs]
    for key in gen_struct[0].keys():
        values = [m[key] for m in gen_struct]
        results['structural_properties'][key] = {
            'mean': np.mean(values),
            'std': np.std(values),
            'min': np.min(values),
            'max': np.max(values)
        }
    
    # Validity scores
    validity_scores = [flowsheet_validity_score(g) for g in generated_graphs]
    for key in validity_scores[0].keys():
        values = [v[key] for v in validity_scores]
        results['validity_scores'][key] = {
            'mean': np.mean(values),
            'pass_rate': np.mean([1 if v > 0.5 else 0 for v in values])
        }
    
    # Compare with reference if provided
    if reference_graphs:
        results['comparison_with_reference'] = compare_structural_distributions(
            generated_graphs, reference_graphs
        )
    
    return results

